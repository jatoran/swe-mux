"""The acquired speech closure's state machine, unpacking, and refusals.

ROADMAP Phase 21 Workstream D. Everything here runs without network: the download
itself is exercised end to end by a frozen probe built from the shipped spec (the
one assertion no unit test can make), and what these tests own is the logic around
it - which states are reported, what a stale closure does, what an unpacker must
refuse, and where the LGPL relink proof now lives.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from swe_mux import voice_runtime
from swe_mux.voice_wheels import CLOSURE_DIGEST, VoiceWheel


def _wheel(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)


def test_a_clean_data_dir_reports_not_downloaded_and_offers_a_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is fetched until asked, and the ask states its cost first."""
    monkeypatch.setattr(voice_runtime, "closure_importable", lambda: False)
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    state = store.status()
    assert state["status"] == "not_downloaded"
    assert state["supported"] is True
    assert state["source"] is None
    assert state["downloaded_bytes"] == 0
    assert state["total_bytes"] > 50 * 1024 * 1024
    assert state["closure"] == CLOSURE_DIGEST
    assert not (tmp_path / "voice-runtime").exists()


def test_an_environment_that_already_has_the_closure_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source checkout with `--extra voice-local` must not be perturbed at all.

    `activate` short-circuits before it looks at disk, `sys.path` is untouched,
    and the reported source says `installed` rather than `downloaded` - a
    distinction a remedy is written against, because one is a property of the
    environment and the other of this daemon's data directory.
    """
    monkeypatch.setattr(voice_runtime, "closure_importable", lambda: True)
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    before = list(voice_runtime.sys.path)
    assert store.activate() is True
    assert voice_runtime.sys.path == before
    assert store.status()["source"] == "installed"


def test_a_tree_built_from_a_different_closure_is_not_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app update that moved the pins must acquire the new closure, not reuse.

    Reported as `not_downloaded` rather than `error`, because nothing failed: the
    remedy is the ordinary press, and calling it an error would send a reader
    looking for a fault that is not there.
    """
    monkeypatch.setattr(voice_runtime, "closure_importable", lambda: False)
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    (store.site / "misaki").mkdir(parents=True)
    (store.site / "misaki" / "__init__.py").write_text("", encoding="utf-8")
    store._write_state({"status": "ready", "closure": "0" * 64})
    assert store.unpacked() is False
    assert store.status()["status"] == "not_downloaded"


def test_a_ready_state_over_a_missing_tree_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ready` on disk with nothing to load is the one case that is a fault."""
    monkeypatch.setattr(voice_runtime, "closure_importable", lambda: False)
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    store._write_state({"status": "ready", "closure": CLOSURE_DIGEST})
    state = store.status()
    assert state["status"] == "error"
    assert "interrupted" in (state["error"] or "")


def test_an_unreadable_state_file_reads_as_never_downloaded(tmp_path: Path) -> None:
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "state.json").write_text("{not json", encoding="utf-8")
    assert store._read_state() == {"status": "not_downloaded"}


def test_activation_puts_the_unpacked_tree_on_sys_path_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism, and its idempotence: a repeated call adds no second entry."""
    # `activate` asks once before touching `sys.path` and once after, then
    # short-circuits on every later call.
    answers = iter([False, True, True])
    monkeypatch.setattr(voice_runtime, "closure_importable", lambda: next(answers, True))
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    (store.site / "misaki").mkdir(parents=True)
    (store.site / "misaki" / "__init__.py").write_text("", encoding="utf-8")
    store._write_state({"status": "ready", "closure": CLOSURE_DIGEST})
    original = list(voice_runtime.sys.path)
    try:
        assert store.activate() is True
        assert voice_runtime.sys.path.count(str(store.site)) == 1
        assert store.activate() is True
        assert voice_runtime.sys.path.count(str(store.site)) == 1
    finally:
        voice_runtime.sys.path[:] = original


def test_an_unsupported_interpreter_reports_error_rather_than_a_pressable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform the pins do not cover has nothing to press.

    Drawing it as `not_downloaded` beside a Download button would be an interface
    that lies: the button can only fail. The remedy is the install-time extra.
    """
    monkeypatch.setattr(voice_runtime, "closure_importable", lambda: False)

    def refuse(*_args: object, **_kwargs: object) -> tuple[VoiceWheel, ...]:
        raise LookupError("no wheel this interpreter can load for: onnxruntime")

    monkeypatch.setattr(voice_runtime, "wheels_for_this_interpreter", refuse)
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    state = store.status()
    assert state["supported"] is False
    assert state["status"] == "error"
    assert "onnxruntime" in (state["error"] or "")
    assert store.start_download() is False


def test_unpacking_promotes_the_data_payload_and_drops_the_rest(tmp_path: Path) -> None:
    """A wheel's `.data/purelib` and `.data/platlib` belong on `sys.path`; `scripts` does not.

    `scripts` in particular would drop console-script launchers pointing at an
    interpreter that need not exist - this store performs no install and must not
    leave anything that looks like one.
    """
    archive = tmp_path / "demo-1.0-py3-none-any.whl"
    _wheel(
        archive,
        {
            "demo/__init__.py": "",
            "demo-1.0.data/purelib/extra/__init__.py": "",
            "demo-1.0.data/platlib/native.pyd": "",
            "demo-1.0.data/scripts/demo.exe": "",
            "demo-1.0.dist-info/METADATA": "Name: demo\n",
        },
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    voice_runtime._extract_wheel(archive, staging)
    assert (staging / "demo" / "__init__.py").is_file()
    assert (staging / "extra" / "__init__.py").is_file()
    assert (staging / "native.pyd").is_file()
    assert (staging / "demo-1.0.dist-info" / "METADATA").is_file()
    assert not (staging / "demo-1.0.data").exists()
    assert not any(staging.rglob("demo.exe"))


def test_unpacking_merges_namespace_packages_from_the_data_payload(tmp_path: Path) -> None:
    """`Path.replace` on a directory fails when the target exists, so the merge is per-file."""
    staging = tmp_path / "staging"
    (staging / "google" / "protobuf").mkdir(parents=True)
    (staging / "google" / "protobuf" / "__init__.py").write_text("", encoding="utf-8")
    archive = tmp_path / "other-1.0-py3-none-any.whl"
    _wheel(archive, {"other-1.0.data/purelib/google/other/__init__.py": ""})
    voice_runtime._extract_wheel(archive, staging)
    assert (staging / "google" / "protobuf" / "__init__.py").is_file()
    assert (staging / "google" / "other" / "__init__.py").is_file()


def test_unpacking_refuses_an_out_of_tree_member(tmp_path: Path) -> None:
    """The payload is hash-pinned, so this cannot currently be hostile.

    Checked anyway, because the alternative is a rule that holds only while the
    pin table is right, and a parent-relative member is never legitimate.
    """
    archive = tmp_path / "evil-1.0-py3-none-any.whl"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escaped.py", "")
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(voice_runtime.VoiceRuntimeError, match="out-of-tree"):
        voice_runtime._extract_wheel(archive, staging)
    assert not (tmp_path / "escaped.py").exists()


def test_the_relink_proof_needs_readable_source(tmp_path: Path) -> None:
    """Where the LGPL obligation lives now that the bundle does not carry it.

    swe-mux no longer distributes `num2words` - the wheel goes from PyPI to the
    user - but the copy that lands still has to be source a recipient can replace,
    which is what `THIRD-PARTY-NOTICES.md` promises.
    """
    site = tmp_path / "site"
    (site / "num2words").mkdir(parents=True)
    with pytest.raises(voice_runtime.VoiceRuntimeError, match="LGPL relink"):
        voice_runtime._verify_relinkable(site)
    (site / "num2words" / "base.py").write_text("", encoding="utf-8")
    voice_runtime._verify_relinkable(site)


def test_a_cached_wheel_is_reverified_rather_than_trusted(tmp_path: Path) -> None:
    """Resumption is at wheel granularity, and a resumed wheel is checked again."""
    import hashlib

    payload = b"pinned bytes"
    path = tmp_path / "cached.whl"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    assert voice_runtime._file_verified(path, len(payload), digest)
    assert not voice_runtime._file_verified(path, len(payload), "0" * 64)
    assert not voice_runtime._file_verified(path, len(payload) + 1, digest)
    assert not voice_runtime._file_verified(tmp_path / "absent.whl", 1, digest)


def test_the_unpack_refuses_a_closure_missing_a_required_module(tmp_path: Path) -> None:
    """A pin table that stopped covering something the voice code imports.

    The failure this converts: without it the tree is promoted, `activate` puts it
    on `sys.path`, `closure_importable` says no, and the store reports `error`
    with "the download was interrupted" - which is the wrong story entirely.
    """
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    cache = store.root / "wheels"
    cache.mkdir(parents=True)
    archive = cache / "misaki-1.0-py3-none-any.whl"
    _wheel(archive, {"misaki/__init__.py": "", "num2words/__init__.py": ""})
    wheels = (VoiceWheel("misaki", "1.0", archive.name, "https://x/y", "0" * 64, 1),)
    with pytest.raises(voice_runtime.VoiceRuntimeError, match="does not contain"):
        store._unpack(wheels, cache)
    assert not store.site.exists()


def test_the_state_file_records_the_closure_it_built(tmp_path: Path) -> None:
    """So a later version can tell "stale" from "absent" without re-hashing 315 MB."""
    store = voice_runtime.VoiceRuntimeStore(tmp_path)
    store._write_state({"status": "ready", "closure": CLOSURE_DIGEST, "wheels": {}})
    recorded = json.loads((store.root / "state.json").read_text(encoding="utf-8"))
    assert recorded["closure"] == CLOSURE_DIGEST


def test_every_acquired_module_the_voice_code_imports_is_probed_for() -> None:
    """Nothing the voice path imports may be outside the readiness probe.

    The direction matters. An acquired module that is imported but *not* probed is
    the silent failure: `activate()` reports the closure ready, the feature is
    offered, and the import raises inside a synthesis worker. The reverse - a
    probed module that no swe-mux file imports directly - is fine and expected:
    `num2words` is imported by `misaki.en`, `thinc` and `blis` by spaCy, and all
    three are exactly what "the closure is present" has to mean.

    Read from the source with `ast` rather than by importing, because importing
    spaCy costs a second and this is the list that decides whether to bother.
    """
    import ast
    import sys as _sys

    root = Path(voice_runtime.__file__).parent
    # Everything the base bundle still carries. An import of one of these is not
    # a claim about the acquired closure.
    shipped = {
        "aiohttp", "cryptography", "mcp", "packaging", "PIL", "psutil", "pydantic",
        "py_vapid", "pywebpush", "pywinpty", "swe_mux", "tree_sitter", "watchfiles",
        "winpty", "yarl",
    }
    imported: set[str] = set()
    for name in ("voice.py", "kokoro_tts.py", "voice_models.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    acquired = imported - shipped - set(_sys.stdlib_module_names) - {"av"}
    assert acquired <= set(voice_runtime.REQUIRED_MODULES), (
        "the voice path imports an acquired module the readiness probe does not "
        f"check: {sorted(acquired - set(voice_runtime.REQUIRED_MODULES))}"
    )


def test_whisper_backend_memoization_can_be_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The store can put `faster_whisper` on `sys.path` after this store answered no.

    Nothing else re-asks, so without `forget_backend` an install that just
    acquired the libraries keeps reporting a missing backend until it restarts.
    """
    from swe_mux.voice_models import WhisperModelStore

    store = WhisperModelStore()
    monkeypatch.setattr(WhisperModelStore, "_import_backend", staticmethod(lambda: False))
    assert store.backend_installed() is False
    monkeypatch.setattr(WhisperModelStore, "_import_backend", staticmethod(lambda: True))
    assert store.backend_installed() is False  # memoized
    store.forget_backend()
    assert store.backend_installed() is True
