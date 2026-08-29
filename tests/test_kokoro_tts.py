"""Kokoro TTS (Phase 10.5): the espeak-free G2P constraint and the repair ladder."""

from __future__ import annotations

import re
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from swe_mux import av_stub, kokoro_tts
from swe_mux.config import load_config
from swe_mux.kokoro_tts import (
    MAX_PHONEME_TOKENS,
    KokoroEngine,
    KokoroError,
    KokoroPaths,
    SpelledWordLog,
    assert_espeak_free,
    spell_out,
    split_compound,
)
from swe_mux.voice_models import (
    ENGLISH_VOICES,
    KOKORO_FILES,
    KokoroModelStore,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_engine(tmp_path: Path, **kwargs: Any) -> KokoroEngine:
    return KokoroEngine(
        KokoroPaths(
            model=tmp_path / "model.onnx",
            tokenizer=tmp_path / "tokenizer.json",
            voices_dir=tmp_path / "voices",
        ),
        **kwargs,
    )


class FakeG2P:
    """Lexicon-only G2P double: known words phonemize, everything else is ❓.

    Phoneme links resolve like the real misaki: `[word](/phonemes/)` speaks its
    phonemes whatever the word is, so the double substitutes a known token.
    """

    KNOWN = {
        "the", "pie", "project", "health", "check", "work", "tree", "passed",
        "and", "is", "clean", "s", "w", "e", "mux", "on", "a", "b", "c",
        "ess", "double", "you", "ee", "why", "pee", "con", "cue",
    }

    def __call__(self, text: str) -> tuple[str, Any]:
        text = kokoro_tts.PHONEME_LINK.sub("pie", text)
        words = [word.strip(".,!?:;").casefold() for word in text.split()]
        phonemes = " ".join(
            "x" if (not word or word in self.KNOWN or word.isdigit()) else "❓"
            for word in words
        )
        return phonemes, None


def test_split_compound_covers_the_measured_failures() -> None:
    assert split_compound("pyproject") == ["pyproject"]  # no boundary to split on
    assert split_compound("swe-mux") == ["swe", "mux"]
    assert split_compound("healthcheck") == ["healthcheck"]
    assert split_compound("HealthCheck") == ["Health", "Check"]
    assert split_compound("worktree_verify") == ["worktree", "verify"]
    assert split_compound("ConPTY") == ["Con", "PTY"]
    assert split_compound("v2ray4") == ["v", "2", "ray", "4"]


def test_spell_out_is_the_unambiguous_last_resort() -> None:
    assert spell_out("ab1") == "eigh bee one"
    assert spell_out("!!") == "!!"  # nothing spellable: keep the token, never drop it


def test_prepare_text_repairs_unknowns_via_lexicon_splitter_then_spelling(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine._g2p = FakeG2P()
    prepared = engine.prepare_text("The pyproject healthcheck passed on swe-mux and is clean.")
    # The lexicon rewrites the measured vocabulary; nothing is silently dropped.
    assert "pie project" in prepared
    assert "health check" in prepared
    assert "S W E" in prepared
    assert "❓" not in engine.phonemize(prepared)


def test_prepare_text_leaves_resolvable_text_untouched(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine._g2p = FakeG2P()
    text = "The work tree is clean."
    assert engine.prepare_text(text) == text


def test_user_lexicon_overrides_the_spelling_floor_and_hot_swaps(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine._g2p = FakeG2P()
    # Without an entry the unknown word hits the spelling floor.
    assert engine.prepare_text("vaultspaces") == spell_out("vaultspaces")
    # A live lexicon change must invalidate the per-word cache, or the spelled
    # resolution above would keep being served until a daemon restart.
    engine.set_lexicon({" Vaultspaces ": " work tree "})
    assert engine.prepare_text("vaultspaces") == "work tree"
    # The lookup casefolds, so any casing of the word hits the same entry.
    assert engine.prepare_text("VAULTSPACES") == "work tree"
    # The built-in project lexicon stays underneath the user map.
    assert engine.prepare_text("pyproject") == "pie project"


def test_spell_out_telemetry_reports_the_word_an_operator_would_respell(
    tmp_path: Path,
) -> None:
    reported: list[str] = []
    engine = make_engine(tmp_path, on_spell_out=reported.append)
    engine._g2p = FakeG2P()
    # A lexicon-resolved word is fixed, not debt: nothing is reported.
    engine.prepare_text("pyproject")
    assert reported == []
    # A compound whose unknown piece hits the floor reports the *top-level*
    # word — the token the operator would actually add to the lexicon.
    engine.prepare_text("clean-qqq")
    assert reported == ["clean-qqq"]
    # Every spoken occurrence reports, including cache hits, so counts track use.
    reported.clear()
    engine.prepare_text("qqq and qqq")
    assert reported == ["qqq", "qqq"]


def test_replacement_pieces_keep_phoneme_links_whole() -> None:
    from swe_mux.kokoro_tts import replacement_pieces

    assert replacement_pieces("vault spaces") == ["vault", "spaces"]
    # A multi-word phoneme link contains spaces; splitting it would corrupt it.
    assert replacement_pieces("[swe-mux](/swˈi mˈʌks/) tool") == [
        "[swe-mux](/swˈi mˈʌks/)",
        "tool",
    ]


def test_phoneme_link_respellings_pass_the_ladder_atomically(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine._g2p = FakeG2P()
    engine.set_lexicon(
        {
            "swemux": "[swemux](/swˈimˈʌks/)",
            # The internal space is the regression: whitespace-splitting this
            # value produced two broken halves and the ladder spelled the word.
            "swe-mux": "[swe-mux](/swˈi mˈʌks/)",
        }
    )
    assert engine.prepare_text("swemux") == "[swemux](/swˈimˈʌks/)"
    assert engine.prepare_text("swe-mux") == "[swe-mux](/swˈi mˈʌks/)"


def test_trailing_punctuation_does_not_defeat_the_lexicon(tmp_path: Path) -> None:
    """A sentence-final unknown token carries its punctuation into the match;
    the lookup must resolve the core and keep the tail for prosody."""
    reported: list[str] = []
    engine = make_engine(tmp_path, on_spell_out=reported.append)
    engine._g2p = FakeG2P()
    assert engine.prepare_text("The pyproject. passed.") == "The pie project. passed."
    assert reported == []
    # The spelling floor reports the core, the word an operator would respell.
    engine.prepare_text("qqq.")
    assert reported == ["qqq"]


def test_check_respelling_mirrors_what_the_ladder_would_speak(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine._g2p = FakeG2P()
    good = engine.check_respelling("vaultspaces", "work tree")
    assert good["ok"] is True and good["unspeakable"] == []
    # The user's actual failure: an invented respelling is itself OOV, so the
    # ladder rejects it and spells the word — the check names the bad piece.
    bad = engine.check_respelling("swe", "qqq tree")
    assert bad["ok"] is False and bad["unspeakable"] == ["qqq"]
    # A value that legitimately repairs through the lexicon is not a failure.
    repairs = engine.check_respelling("tool", "pyproject")
    assert repairs["ok"] is True and repairs["unspeakable"] == []
    assert repairs["spoken_as"] == "pie project"
    # Self-reference can only spell.
    circular = engine.check_respelling("qqq", "qqq")
    assert circular["ok"] is False and circular["unspeakable"] == ["qqq"]
    # A phoneme link is verified whole and passes.
    link = engine.check_respelling("swe", "[swe](/swˈi/)")
    assert link["ok"] is True and link["unspeakable"] == []


def test_build_respelling_links_only_the_unpronounceable_pieces(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine._g2p = FakeG2P()
    # Known words pass through as text; the OOV piece becomes an exact link.
    built = engine.build_respelling("swe", "swee tree")
    assert built["ok"] is True
    assert built["value"] == "[swee](/swˈi/) tree"
    # An empty value reads the word itself as its phonetic spelling — the
    # zero-typing path for the "words I had to spell" list.
    built = engine.build_respelling("swemux", "")
    assert built["ok"] is True
    assert built["value"] == "[swemux](/swˈɛmʌks/)"
    # A fully pronounceable value is returned unchanged.
    built = engine.build_respelling("x", "work tree")
    assert built["ok"] is True and built["value"] == "work tree"
    # Unmappable input is a verdict, not a guess.
    built = engine.build_respelling("x", "it's")
    assert built["ok"] is False and built["value"] is None
    assert "it's" in str(built["diagnostic"])


def test_audition_report_suppression_keeps_telemetry_clean(tmp_path: Path) -> None:
    reported: list[str] = []
    engine = make_engine(tmp_path, on_spell_out=reported.append)
    engine._g2p = FakeG2P()
    engine.prepare_text("qqq", report=False)
    assert reported == []
    # check_respelling never reports either — it is an audition, not speech.
    engine.check_respelling("swe", "zzz")
    assert reported == []
    engine.prepare_text("qqq")
    assert reported == ["qqq"]


def test_spell_out_reporter_failure_never_breaks_speech(tmp_path: Path) -> None:
    def explode(word: str) -> None:
        raise RuntimeError("telemetry sink down")

    engine = make_engine(tmp_path, on_spell_out=explode)
    engine._g2p = FakeG2P()
    assert engine.prepare_text("qqq") == spell_out("qqq")


def test_spelled_word_log_dedupes_counts_persists_and_prunes(tmp_path: Path) -> None:
    path = tmp_path / "voice" / "spelled_words.json"
    log = SpelledWordLog(path)
    log.record("Vaultspaces")
    log.record("vaultspaces ")
    log.record("govspend")
    entries = log.entries()
    assert sorted(item["word"] for item in entries) == ["govspend", "vaultspaces"]
    by_word = {item["word"]: item for item in entries}
    assert by_word["vaultspaces"]["count"] == 2
    assert by_word["vaultspaces"]["last_seen"] >= by_word["vaultspaces"]["first_seen"]
    # Durable: a fresh instance reads the same entries back from disk.
    reloaded = SpelledWordLog(path)
    assert {item["word"] for item in reloaded.entries()} == {"vaultspaces", "govspend"}
    # A lexicon entry pays the debt: covered words leave the list, on disk too.
    reloaded.discard({"VaultSpaces": "vault spaces"})
    assert {item["word"] for item in reloaded.entries()} == {"govspend"}
    assert {item["word"] for item in SpelledWordLog(path).entries()} == {"govspend"}
    # Unspeakable input is refused rather than stored.
    reloaded.record("   ")
    reloaded.record("x" * 61)
    assert len(reloaded.entries()) == 1


def test_spelled_word_log_is_bounded(tmp_path: Path) -> None:
    log = SpelledWordLog(tmp_path / "spelled.json")
    for index in range(SpelledWordLog.CAP + 25):
        log.record(f"word{index}")
    assert len(log.entries()) == SpelledWordLog.CAP


def test_spelled_word_log_survives_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "spelled.json"
    path.write_text("{not json", encoding="utf-8")
    log = SpelledWordLog(path)
    assert log.entries() == []
    log.record("vaultspaces")
    assert [item["word"] for item in log.entries()] == ["vaultspaces"]


def test_espeak_presence_fails_construction_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    real = importlib.util.find_spec

    def fake(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "espeakng_loader":
            return object()
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    with pytest.raises(KokoroError, match="espeak"):
        assert_espeak_free()


def test_token_chunking_bounds_every_chunk(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine._vocab = {"a": 1, "b": 2, ".": 3}
    phonemes = ("ab" * 400) + "." + ("ba" * 400)
    chunks = engine._token_chunks(phonemes)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= MAX_PHONEME_TOKENS for chunk in chunks)
    assert sum(len(chunk) for chunk in chunks) == len(phonemes)


# --------------------------------------------------------------------------- #
# Model acquisition
# --------------------------------------------------------------------------- #


def test_manifest_pins_every_english_voice() -> None:
    for voice in ENGLISH_VOICES:
        size, sha = KOKORO_FILES[f"voices/{voice}.bin"]
        assert size == 522240 and len(sha) == 64


def test_partial_or_tampered_download_is_never_ready(tmp_path: Path) -> None:
    store = KokoroModelStore(tmp_path)
    assert store.status()["status"] == "not_downloaded"
    assert store.ready() is False
    # Files on disk without a verified `ready` state are a partial download.
    store.install.model.parent.mkdir(parents=True, exist_ok=True)
    store.install.model.write_bytes(b"not a model")
    store.install.tokenizer.write_text("{}", encoding="utf-8")
    assert store.ready() is False
    # A state file claiming ready still fails the size check on the real pin.
    store._write_state({"status": "ready", "revision": store.status()["revision"]})
    assert store.ready() is False
    # An interrupted download (state says downloading, no task) reads as error.
    store._write_state({"status": "downloading", "revision": store.status()["revision"]})
    assert store.status()["status"] == "error"


def test_file_verification_requires_exact_size_and_hash(tmp_path: Path) -> None:
    target = tmp_path / "voice.bin"
    target.write_bytes(b"z" * 16)
    import hashlib

    sha = hashlib.sha256(b"z" * 16).hexdigest()
    assert KokoroModelStore._file_verified(target, 16, sha) is True
    assert KokoroModelStore._file_verified(target, 17, sha) is False
    assert KokoroModelStore._file_verified(target, 16, "0" * 64) is False


# --------------------------------------------------------------------------- #
# Config migration and the av stub
# --------------------------------------------------------------------------- #


def test_edge_engine_config_migrates_to_the_os_voice(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('schema_version = 25\ntts_engine = "edge"\n', encoding="utf-8")
    config = load_config(path)
    assert config.tts_engine == "sapi"
    # A deliberate engine choice survives the migration untouched.
    chosen = tmp_path / "chosen" / "config.toml"
    chosen.parent.mkdir()
    chosen.write_text('schema_version = 25\ntts_engine = "sapi"\n', encoding="utf-8")
    assert load_config(chosen).tts_engine == "sapi"


def test_av_runtime_stub_satisfies_import_and_refuses_use() -> None:
    hook = REPO_ROOT / "packaging" / "rthook_av_stub.py"
    saved = sys.modules.pop("av", None)
    try:
        runpy.run_path(str(hook))
        stub = sys.modules["av"]
        with pytest.raises(RuntimeError, match="not installed"):
            _ = stub.open  # any attribute use must fail loudly
    finally:
        sys.modules.pop("av", None)
        if saved is not None:
            sys.modules["av"] = saved


def test_the_frozen_hook_installs_the_same_stub_the_wheel_does() -> None:
    """One definition, two entry points.

    The frozen app reaches the stub through a PyInstaller runtime hook and a
    source/wheel install reaches it through `voice.py`. Two copies of a module
    that must behave identically is what drifts, and the failure mode is the
    worst kind - dictation working in dev and not in the app - so the hook is
    held to being a call into the shared module rather than a second stub.
    """
    hook = (REPO_ROOT / "packaging" / "rthook_av_stub.py").read_text(encoding="utf-8")
    assert "from swe_mux.av_stub import install" in hook
    assert "types.ModuleType" not in hook


def test_av_stub_survives_introspection_but_refuses_pyav_attributes() -> None:
    """Dunders answer as absent; PyAV attributes raise.

    `repr()` of a module reads `__file__`, so a stub that raised on every name
    turned any log line or traceback mentioning it into a RuntimeError from
    inside the stub - burying whatever was actually being diagnosed.
    """
    stub = av_stub.build()
    assert repr(stub) and stub.__name__ == "av"
    assert getattr(stub, "__file__", None) is None
    assert getattr(stub, "__path__", None) is None
    for attribute in ("open", "audio", "error"):  # what faster_whisper.audio uses
        with pytest.raises(RuntimeError, match="not installed"):
            getattr(stub, attribute)


def test_av_stub_install_is_idempotent_and_never_evicts_a_real_pyav() -> None:
    saved = sys.modules.pop("av", None)
    try:
        first = av_stub.install()
        assert sys.modules["av"] is first
        assert av_stub.install() is first  # idempotent
        sentinel = object()
        sys.modules["av"] = sentinel  # type: ignore[assignment]
        assert av_stub.install() is sentinel  # setdefault, not overwrite
    finally:
        sys.modules.pop("av", None)
        if saved is not None:
            sys.modules["av"] = saved


def test_voice_installs_the_stub_before_importing_faster_whisper() -> None:
    """The source-mode half of the fix, pinned by source order.

    `faster_whisper/audio.py` runs `import av` at module scope, and PyAV is not
    in the resolved closure, so an `av_stub.install()` that moved below the
    import would turn local dictation into an ImportError on every install that
    does not happen to have PyAV lying around.
    """
    source = (REPO_ROOT / "src" / "swe_mux" / "voice.py").read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\s*(?:from faster_whisper import|import faster_whisper)\b", re.MULTILINE
    )
    imports = list(pattern.finditer(source))
    assert imports, "voice.py no longer imports faster_whisper; this test is stale"
    for match in imports:
        install = source.rfind("av_stub.install()", 0, match.start())
        assert install != -1, f"no stub install precedes {match.group(0).strip()!r}"
        # And no *earlier* faster_whisper import sits between the two, which
        # would import `av` before the stub was in place.
        assert not pattern.search(source, install, match.start())


def test_spec_excludes_av_and_installs_the_stub() -> None:
    """The bundle gate's static half; build_desktop.verify_no_gpl_av is the dynamic half."""
    spec = (REPO_ROOT / "packaging" / "swe_mux.spec").read_text(encoding="utf-8")
    # Matched by membership rather than by the whole literal: the clause also
    # carries `tkinter` and the splatted acquired voice closure since 2026-08-29,
    # and an exact-string assertion here would fail on every unrelated addition
    # while proving nothing more.
    clause = spec.split("excludes=[")[1].split("]")[0]
    assert '"av"' in clause and '"edge_tts"' in clause
    assert "rthook_av_stub.py" in spec


def test_project_g2p_measurement_holds() -> None:
    """The real misaki G2P, espeak-free, resolves this project's vocabulary.

    This is the live half of the constraint: `assert_espeak_free` passing means
    no GPL phonemizer is installed, and the repair ladder must still produce a
    fully resolvable sentence for the words the 2026-08-17 audit measured.
    """
    pytest.importorskip("misaki")
    assert_espeak_free()
    engine = make_engine(Path("."))
    prepared = engine.prepare_text(
        "The pyproject healthcheck passed on ConPTY, and the swe-mux worktree is clean."
    )
    phonemes, _tokens = engine._ensure_g2p()(prepared)
    assert kokoro_tts.UNKNOWN_TOKEN not in phonemes


def test_real_g2p_phoneme_links_and_punctuated_lexicon_hits() -> None:
    """The 2026-08-18 live failures against the real misaki G2P.

    "swee" is not in misaki's dictionary, so the plain respelling the user
    tried was rejected by re-verification and the word spelled anyway; the
    phoneme-link form must pass. And a sentence-final "vaultspaces." must hit
    the "vaultspaces" lexicon entry despite its attached punctuation.
    """
    pytest.importorskip("misaki")
    assert_espeak_free()
    engine = make_engine(Path("."))
    assert engine.check_respelling("swe", "swee")["ok"] is False
    assert engine.check_respelling("swe", "swee")["unspeakable"] == ["swee"]
    assert engine.check_respelling("swe", "[swe](/swˈi/)")["ok"] is True
    engine.set_lexicon({"swe": "[swe](/swˈi/)", "vaultspaces": "vault spaces"})
    prepared = engine.prepare_text("Opened vaultspaces. Then swe-mux ran.")
    assert "vault spaces." in prepared
    assert "[swe](/swˈi/)" in prepared
    phonemes, _tokens = engine._ensure_g2p()(prepared)
    assert kokoro_tts.UNKNOWN_TOKEN not in phonemes
    # The builder turns the user's own failed spelling into a passing link.
    built = engine.build_respelling("swe", "swee")
    assert built["ok"] is True and built["value"] == "[swee](/swˈi/)"
    built = engine.build_respelling("chronotron", "")
    assert built["ok"] is True
    assert str(built["value"]).startswith("[chronotron](/")


def test_an_absent_g2p_model_is_refused_and_never_pip_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """misaki would run `pip install` from inside the synthesis path. It must not.

    `misaki.en.G2P.__init__` reads

        if not spacy.util.is_package(name): spacy.cli.download(name)

    and `spacy.cli.download` shells out to pip. In a source checkout that writes
    into the venv unasked; in the frozen app there is no pip to shell to at all;
    and on either, it happens on a worker thread while somebody is waiting for a
    sentence to be spoken. The model became a first-use asset when it stopped
    being a publishable dependency, so its absence is now a legitimate state and
    has to answer with a remedy rather than with an unrequested install.

    `en.G2P` is asserted never to be reached, which is the only form of this test
    that would fail if the check were removed: on this machine the model *is*
    installed, so a test that only inspected the error message would pass with
    the guard deleted.
    """
    pytest.importorskip("misaki")
    from misaki import en

    def never(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - the point
        raise AssertionError("en.G2P was constructed with no model resolvable")

    monkeypatch.setattr(kokoro_tts, "g2p_model_installed", lambda: False)
    monkeypatch.setattr(en, "G2P", never)
    engine = make_engine(Path("."))
    with pytest.raises(KokoroError) as raised:
        engine._ensure_g2p()
    message = str(raised.value)
    assert "spaCy English model" in message
    # Both audiences: a press for an installed copy, a command for a checkout.
    assert "Settings" in message
    assert "uv sync --group g2p-model" in message
