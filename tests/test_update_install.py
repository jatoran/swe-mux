"""The frozen-app updater: what it refuses, and what it hands to the swap.

Every test here is about a refusal, and that is the right shape for this feature.
The happy path replaces the application the operator is using; the interesting
question is never "does a good download install", it is "does anything that is
not a good download get anywhere near the swap". So the file is organized around
the six ways an install must stop - a bad hash, a short body, an unreachable
host, a manifest this build cannot read, a release needing a new PTY supervisor,
and an install that is not a frozen app at all - plus the proof that the one
remaining case reaches `redeploy_desktop.py` and nothing else does.

None of it downloads anything or builds a bundle. The manifest arrives through
the same injected `Fetcher` the update check uses, artifact bytes arrive through
an injected `Downloader`, and the archives are real zips built by the real
packaging writer (`packaging/package_desktop_release.py`) over a directory
holding two small files - which is what keeps the naming contract and the
metadata contract tested rather than restated.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.bundle_archive import (
    ARCHIVE_ROOT,
    TAR_GZ_SUFFIX,
    ArchiveError,
    read_archive_metadata,
    validate_members,
)
from swe_mux.bundle_metadata import (
    BUNDLE_METADATA_MALFORMED,
    BUNDLE_METADATA_MISSING,
    BUNDLE_METADATA_UNSUPPORTED_SCHEMA,
    bundle_metadata,
    parse_bundle_metadata,
    read_bundle_metadata,
    write_bundle_metadata,
)
from swe_mux.config import Config
from swe_mux.routes import update as update_routes
from swe_mux.update_check import MANIFEST_URL, parse_github_release, parse_manifest
from swe_mux.update_install import (
    INSTALL_FROZEN,
    INSTALL_SOURCE,
    PHASE_HANDED_OFF,
    PHASE_REFUSED,
    REASON_ARCHIVE_INVALID,
    REASON_BUNDLE_METADATA_MISSING,
    REASON_HASH_MISMATCH,
    REASON_MALFORMED,
    REASON_NO_ARTIFACT,
    REASON_NO_SUPERVISOR,
    REASON_SOURCE_INSTALL,
    REASON_SUPERVISOR_UNKNOWN,
    REASON_SUPERVISOR_UPDATE_REQUIRED,
    REASON_TRUNCATED,
    REASON_UNREACHABLE,
    REASON_UNSUPPORTED_SCHEMA,
    REASON_VERSION_MISMATCH,
    DownloadOutcome,
    InstallKind,
    UpdateInstaller,
    UpdateRefused,
    detect_install_kind,
    release_archive_name,
    release_platform_tag,
    running_supervisor_protocol,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

import package_desktop_release  # noqa: E402 - packaging module, added to path above

CURRENT = "0.1.0"
NEXT = "0.2.0"
SUPERVISOR_PROTOCOL = 1


# --- fixtures and fakes -------------------------------------------------------


class FakeFetch:
    """The manifest fetcher, counting calls so "no request" is measured."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    async def __call__(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> tuple[int, bytes]:
        self.calls.append(url)
        answer = self.answers.get(url)
        if answer is None:
            raise OSError("unreachable")
        if isinstance(answer, int):
            return answer, b""
        if isinstance(answer, bytes):
            return 200, answer
        return 200, json.dumps(answer).encode("utf-8")


class FakeDownload:
    """Streams canned bytes, and can lie about how many it was going to send."""

    def __init__(
        self,
        payload: bytes = b"",
        *,
        declared: int | None = None,
        status: int = 200,
        raises: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.declared = declared
        self.status = status
        self.raises = raises
        self.calls: list[str] = []

    async def __call__(
        self,
        url: str,
        *,
        write: Callable[[bytes], None],
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> DownloadOutcome:
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        if self.status != 200:
            return DownloadOutcome(status=self.status, declared_bytes=None, received_bytes=0)
        write(self.payload)
        return DownloadOutcome(
            status=self.status,
            declared_bytes=self.declared if self.declared is not None else len(self.payload),
            received_bytes=len(self.payload),
        )


class Handoff:
    """Stands in for spawning the redeploy script. Records what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def __call__(self, archive: Path, version: str) -> int:
        self.calls.append((archive, version))
        return 4242


def make_bundle(
    directory: Path, *, version: str = NEXT, protocol: int = SUPERVISOR_PROTOCOL
) -> Path:
    """A directory shaped like a built bundle, small enough to zip in a test."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "swe-mux.exe").write_bytes(b"MZ not really an executable")
    (directory / "_internal").mkdir(exist_ok=True)
    (directory / "_internal" / "base_library.zip").write_bytes(b"payload")
    write_bundle_metadata(
        directory,
        bundle_metadata(
            version=version, supervisor_protocol=protocol, platform=release_platform_tag()
        ),
    )
    return directory


def make_archive(
    tmp_path: Path, *, version: str = NEXT, protocol: int = SUPERVISOR_PROTOCOL
) -> Path:
    """A real release archive, produced by the real packaging writer."""
    bundle = make_bundle(tmp_path / "build" / "swe-mux", version=version, protocol=protocol)
    return package_desktop_release.build_archive(bundle, tmp_path / "out")


def write_plain_archive(path: Path, members: dict[str, bytes]) -> Path:
    """A structurally valid release archive in *this host's* container format.

    For the cases the real writer cannot produce - an archive deliberately
    missing its `bundle.json`, say. `bundle_archive` chooses its reader from the
    file's suffix and never by sniffing content, so the container has to match
    the name `release_archive_name` gives it or the archive is refused as
    unreadable before the property under test is reached.
    """
    if path.name.endswith(TAR_GZ_SUFFIX):
        with tarfile.open(path, "w:gz") as tar:
            for name, payload in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    else:
        with zipfile.ZipFile(path, "w") as bundle:
            for name, payload in members.items():
                bundle.writestr(name, payload)
    return path


def manifest(
    version: str = NEXT, *, artifacts: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "schema": 1,
        "version": version,
        "tag": f"v{version}",
        "published": "2026-08-28T00:00:00Z",
        "changelog": f"https://github.com/jatoran/swe-mux/releases/tag/v{version}",
        "artifacts": artifacts if artifacts is not None else [],
    }


def artifact_entry(archive: Path, *, sha256: str | None = None) -> dict[str, str]:
    digest = sha256 if sha256 is not None else hashlib.sha256(archive.read_bytes()).hexdigest()
    return {
        "name": archive.name,
        "url": f"https://github.com/jatoran/swe-mux/releases/download/v{NEXT}/{archive.name}",
        "sha256": digest,
    }


def write_supervisor(data_dir: Path, *, protocol: int | None = SUPERVISOR_PROTOCOL) -> None:
    payload: dict[str, Any] = {"pid": 1234, "port": 5000, "token": "x", "started_at": 1.0}
    if protocol is not None:
        payload["protocol"] = protocol
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "supervisor.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def swap_tool_available(monkeypatch: Any) -> None:
    """Pretend the redeploy script and `uv` are reachable.

    Stubbed rather than relied on: the preflight's answer would otherwise depend
    on whether the machine running the suite happens to have `uv` on PATH, which
    is a fact about the runner and not about the updater. The one test that cares
    overrides this.
    """
    monkeypatch.setattr(
        "swe_mux.update_install.redeploy_source_root", lambda: Path("/checkout")
    )
    monkeypatch.setattr("swe_mux.update_install.shutil.which", lambda _name: "uv")
    monkeypatch.setattr("swe_mux.update_install.redeploy_lock_pid", lambda _config: None)


def build(
    tmp_path: Path,
    *,
    fetch: FakeFetch,
    download: FakeDownload | None = None,
    handoff: Handoff | None = None,
    frozen: bool = True,
) -> UpdateInstaller:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    kind = (
        InstallKind(
            kind=INSTALL_FROZEN,
            bundle_root=tmp_path / "dist" / "swe-mux",
            upgrade_command="",
        )
        if frozen
        else InstallKind(
            kind=INSTALL_SOURCE, bundle_root=None, upgrade_command="uv tool upgrade swe-mux"
        )
    )
    return UpdateInstaller(
        Config(data_dir=data_dir),
        current_version=CURRENT,
        fetch=fetch,
        download=download or FakeDownload(),
        install_kind=kind,
        handoff=handoff or Handoff(),
        platform_tag=release_platform_tag(),
    )


async def run_install(installer: UpdateInstaller, version: str = NEXT) -> dict[str, Any]:
    """Start an install and let it finish, returning the final snapshot."""
    await installer.start(version)
    await installer.wait()
    return installer.snapshot()


# --- the six refusals ---------------------------------------------------------


async def test_a_hash_mismatch_stages_nothing_and_deletes_the_download(
    tmp_path: Path,
) -> None:
    # The single most important assertion in this file: a body that does not
    # match the manifest's digest must never become a file the swap can see.
    archive = make_archive(tmp_path)
    entry = artifact_entry(archive, sha256="0" * 64)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[entry])})
    handoff = Handoff()
    installer = build(
        tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()), handoff=handoff
    )
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_REFUSED
    assert snapshot["reason"] == REASON_HASH_MISMATCH
    assert handoff.calls == []
    downloads = installer.downloads_dir
    assert not (downloads / archive.name).exists()
    assert not (downloads / f"{archive.name}.part").exists()
    assert list(downloads.iterdir()) == []


async def test_a_truncated_download_is_named_as_truncated_not_as_a_bad_hash(
    tmp_path: Path,
) -> None:
    # Both would be caught by the digest, and they are still different facts: a
    # short body is a network event worth retrying, a wrong digest over a
    # complete one never becomes right by trying again.
    archive = make_archive(tmp_path)
    body = archive.read_bytes()
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    installer = build(
        tmp_path,
        fetch=fetch,
        download=FakeDownload(body[: len(body) // 2], declared=len(body)),
        handoff=handoff,
    )
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_REFUSED
    assert snapshot["reason"] == REASON_TRUNCATED
    assert str(len(body)) in snapshot["message"]
    assert handoff.calls == []
    assert list(installer.downloads_dir.iterdir()) == []


async def test_an_unreachable_manifest_refuses_before_anything_is_fetched(
    tmp_path: Path,
) -> None:
    fetch = FakeFetch({})  # every URL raises
    download = FakeDownload()
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_REFUSED
    assert snapshot["reason"] == REASON_UNREACHABLE
    # The point of refusing here rather than later: no artifact request was made.
    assert download.calls == []


async def test_a_manifest_answering_a_non_200_is_unreachable_rather_than_malformed(
    tmp_path: Path,
) -> None:
    installer = build(tmp_path, fetch=FakeFetch({MANIFEST_URL: 503}))
    write_supervisor(Path(installer._config.data_dir))
    snapshot = await run_install(installer)
    assert snapshot["reason"] == REASON_UNREACHABLE


async def test_a_manifest_that_is_not_json_is_malformed(tmp_path: Path) -> None:
    installer = build(tmp_path, fetch=FakeFetch({MANIFEST_URL: b"<html>captive portal</html>"}))
    write_supervisor(Path(installer._config.data_dir))
    snapshot = await run_install(installer)
    assert snapshot["reason"] == REASON_MALFORMED


async def test_a_schema_this_build_never_heard_of_stops_the_install(tmp_path: Path) -> None:
    # The same rule the check follows, and it matters more here: guessing at a
    # future manifest's fields would mean downloading whatever a repurposed
    # `artifacts` list happened to name.
    archive = make_archive(tmp_path)
    future = {**manifest(artifacts=[artifact_entry(archive)]), "schema": 99}
    download = FakeDownload(archive.read_bytes())
    installer = build(tmp_path, fetch=FakeFetch({MANIFEST_URL: future}), download=download)
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["reason"] == REASON_UNSUPPORTED_SCHEMA
    assert download.calls == []


async def test_a_release_needing_a_new_supervisor_is_refused_not_installed(
    tmp_path: Path,
) -> None:
    # The property the whole feature is built around: the swap preserves sessions
    # only because the supervisor outlives it, and a release whose daemon speaks
    # a different supervisor protocol cannot be installed without reaping the
    # fleet. So it stops here, with the manual flow named.
    archive = make_archive(tmp_path, protocol=SUPERVISOR_PROTOCOL + 1)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    installer = build(
        tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()), handoff=handoff
    )
    write_supervisor(Path(installer._config.data_dir), protocol=SUPERVISOR_PROTOCOL)

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_REFUSED
    assert snapshot["reason"] == REASON_SUPERVISOR_UPDATE_REQUIRED
    assert "muxd --shutdown" in snapshot["message"]
    assert handoff.calls == []
    # The verified archive is kept: the operator may still install it by hand,
    # and re-downloading hundreds of megabytes to do so would be a waste.
    assert (installer.downloads_dir / archive.name).is_file()


async def test_a_supervisor_downgrade_is_refused_for_the_same_reason_as_a_bump(
    tmp_path: Path,
) -> None:
    # `!=`, not `>`. The supervisor's own `hello` refuses any mismatch, so an
    # older protocol strands the fleet exactly as a newer one does.
    archive = make_archive(tmp_path, protocol=SUPERVISOR_PROTOCOL)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir), protocol=SUPERVISOR_PROTOCOL + 5)

    snapshot = await run_install(installer)

    assert snapshot["reason"] == REASON_SUPERVISOR_UPDATE_REQUIRED


async def test_a_supervisor_whose_protocol_cannot_be_read_is_a_refusal_not_a_default(
    tmp_path: Path,
) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir), protocol=None)

    snapshot = await run_install(installer)

    assert snapshot["reason"] == REASON_SUPERVISOR_UNKNOWN


async def test_no_running_supervisor_refuses_because_a_swap_would_reap_sessions(
    tmp_path: Path,
) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))

    snapshot = await run_install(installer)

    assert snapshot["reason"] == REASON_NO_SUPERVISOR


async def test_a_source_install_declines_to_swap_and_says_what_to_run(
    tmp_path: Path,
) -> None:
    # The case most operators meet first. A `uv tool install` has no bundle, and
    # answering "updating…" would be a lie with no swap behind it.
    fetch = FakeFetch({MANIFEST_URL: manifest()})
    installer = build(tmp_path, fetch=fetch, frozen=False)

    with pytest.raises(UpdateRefused) as refusal:
        await installer.start(NEXT)

    assert refusal.value.reason == REASON_SOURCE_INSTALL
    assert "uv tool upgrade swe-mux" in refusal.value.message
    # Refused synchronously, so nothing was fetched at all.
    assert fetch.calls == []
    # ...and still recorded, so `mux update` can say why nothing happened.
    assert installer.snapshot()["phase"] == PHASE_REFUSED


# --- the path that does install ------------------------------------------------


async def test_a_verified_archive_reaches_the_staged_swap(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    installer = build(
        tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()), handoff=handoff
    )
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert len(handoff.calls) == 1
    handed, version = handoff.calls[0]
    assert version == NEXT
    assert handed == installer.downloads_dir / archive.name
    # What was handed over is byte-identical to what the manifest hashed.
    assert handed.read_bytes() == archive.read_bytes()


async def test_an_already_verified_archive_is_reused_rather_than_refetched(
    tmp_path: Path,
) -> None:
    # The resume that is actually worth having: the daemon restarted, the
    # operator pressed again, and a 400 MB transfer does not repeat.
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    download = FakeDownload(archive.read_bytes())
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir))
    await run_install(installer)
    assert len(download.calls) == 1

    second = build(tmp_path, fetch=fetch, download=download)
    await run_install(second)

    assert second.snapshot()["phase"] == PHASE_HANDED_OFF
    assert len(download.calls) == 1


async def test_a_stale_file_under_the_artifact_name_is_replaced_not_trusted(
    tmp_path: Path,
) -> None:
    # A file whose digest is wrong is not a partial download - keeping it would
    # make every future attempt fail identically.
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))
    installer.downloads_dir.mkdir(parents=True, exist_ok=True)
    (installer.downloads_dir / archive.name).write_bytes(b"an old truncated attempt")

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert (installer.downloads_dir / archive.name).read_bytes() == archive.read_bytes()


async def test_a_download_that_dies_part_way_leaves_no_part_file(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    installer = build(
        tmp_path,
        fetch=fetch,
        download=FakeDownload(raises=ConnectionResetError("peer went away")),
        handoff=handoff,
    )
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_REFUSED
    assert handoff.calls == []
    assert list(installer.downloads_dir.iterdir()) == []


# --- what the manifest has to say ---------------------------------------------


async def test_a_release_with_no_artifact_for_this_platform_says_so(tmp_path: Path) -> None:
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[])})
    download = FakeDownload()
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["reason"] == REASON_NO_ARTIFACT
    assert release_archive_name(NEXT, release_platform_tag()) in snapshot["message"]
    assert download.calls == []


async def test_a_manifest_that_moved_on_refuses_rather_than_installing_something_else(
    tmp_path: Path,
) -> None:
    # Consent is about a version, not about "whatever is latest": between reading
    # a banner for 0.2.0 and pressing the button, a 0.3.0 may have been cut.
    archive = make_archive(tmp_path, version="0.3.0")
    fetch = FakeFetch({MANIFEST_URL: manifest("0.3.0", artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer, version=NEXT)

    assert snapshot["reason"] == REASON_VERSION_MISMATCH


def test_the_manifest_parser_keeps_only_fully_described_artifacts() -> None:
    release, reason = parse_manifest(
        {
            **manifest(),
            "artifacts": [
                {"name": "a.zip", "url": "https://x/a.zip", "sha256": "AB12"},
                {"name": "b.zip", "url": "https://x/b.zip"},  # no hash
                {"name": "", "url": "https://x/c.zip", "sha256": "cd"},  # no name
                "not a dict",
            ],
        }
    )
    assert reason == "ok"
    assert release is not None
    assert [artifact.name for artifact in release.artifacts] == ["a.zip"]
    # Digests are compared lowercase, so they are normalized once, on the way in.
    assert release.artifacts[0].sha256 == "ab12"


def test_the_github_fallback_publishes_no_artifacts_and_therefore_no_install() -> None:
    release, reason = parse_github_release(
        {
            "tag_name": "v0.2.0",
            "html_url": "https://github.com/jatoran/swe-mux/releases/tag/v0.2.0",
            "published_at": "2026-08-28T00:00:00Z",
            "assets": [{"name": "swe-mux.zip", "browser_download_url": "https://x/y.zip"}],
        }
    )
    assert reason == "ok"
    assert release is not None
    assert release.artifacts == ()


def test_artifacts_are_not_persisted_by_the_check() -> None:
    # A stored hash is a claim about bytes nobody is holding, and the release
    # workflow re-uploads with `--clobber`. The updater re-fetches instead.
    release, _ = parse_manifest(
        manifest(artifacts=[{"name": "a.zip", "url": "https://x/a.zip", "sha256": "ab"}])
    )
    assert release is not None
    assert "artifacts" not in release.as_dict()


# --- the archive's shape -------------------------------------------------------


def test_an_archive_that_would_write_outside_its_own_tree_is_refused(tmp_path: Path) -> None:
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("swe-mux/ok.txt", "fine")
        bundle.writestr("../../Windows/System32/evil.dll", "not fine")
    with pytest.raises(ArchiveError) as refusal:
        read_archive_metadata(evil)
    assert refusal.value.reason == REASON_ARCHIVE_INVALID


def test_an_archive_with_an_absolute_path_is_refused() -> None:
    with pytest.raises(ArchiveError):
        validate_members(["C:/Windows/System32/evil.dll"])
    with pytest.raises(ArchiveError):
        validate_members(["/etc/passwd"])


def test_an_archive_rooted_somewhere_other_than_swe_mux_is_refused() -> None:
    with pytest.raises(ArchiveError):
        validate_members(["something-else/swe-mux.exe"])


async def test_an_archive_without_bundle_metadata_cannot_be_installed(
    tmp_path: Path,
) -> None:
    # Not a corner case: it is what an archive built before this contract existed
    # looks like, and it is exactly the archive whose supervisor requirement
    # nobody can determine.
    # The *container* has to be this host's too, not just the name. The name was
    # fixed first - `release_archive_name`, because the suffix is per host
    # (`.zip` on Windows, `.tar.gz` on macOS and Linux) and a hardcoded `.zip`
    # named an artifact no POSIX host would ever look for - but the bytes stayed
    # a zip, and `bundle_archive` dispatches on the suffix by design: a zip
    # inside a `.tar.gz` is `archive_invalid` before any metadata question is
    # reached. That is the correct refusal for the file the fixture built, and it
    # is a different one from the refusal under test.
    plain = write_plain_archive(
        tmp_path / release_archive_name(NEXT), {f"{ARCHIVE_ROOT}/swe-mux.exe": b"MZ"}
    )
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(plain)])})
    handoff = Handoff()
    installer = build(
        tmp_path, fetch=fetch, download=FakeDownload(plain.read_bytes()), handoff=handoff
    )
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["reason"] == REASON_BUNDLE_METADATA_MISSING
    assert handoff.calls == []


def test_bundle_metadata_honours_its_schema_before_reading_a_field() -> None:
    ok, reason = parse_bundle_metadata(
        {"schema": 1, "version": "0.2.0", "supervisor_protocol": 1, "platform": "windows-x64"}
    )
    assert reason == "ok"
    assert ok is not None and ok.supervisor_protocol == 1
    _, future = parse_bundle_metadata({"schema": 2, "version": "9.9.9", "supervisor_protocol": 1})
    assert future == BUNDLE_METADATA_UNSUPPORTED_SCHEMA
    # A protocol that is not an integer is malformed rather than defaulted: a
    # default here would be an assumption about whether sessions survive.
    _, bad = parse_bundle_metadata({"schema": 1, "version": "0.2.0", "supervisor_protocol": "one"})
    assert bad == BUNDLE_METADATA_MALFORMED
    _, absent = parse_bundle_metadata({"schema": 1, "version": "0.2.0"})
    assert absent == BUNDLE_METADATA_MALFORMED


def test_a_built_bundle_describes_itself(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "swe-mux")
    metadata, reason = read_bundle_metadata(bundle)
    assert reason == "ok"
    assert metadata is not None
    assert metadata.version == NEXT
    assert metadata.supervisor_protocol == SUPERVISOR_PROTOCOL
    assert metadata.platform == release_platform_tag()
    missing, why = read_bundle_metadata(tmp_path / "nowhere")
    assert (missing, why) == (None, BUNDLE_METADATA_MISSING)


def test_the_archive_name_is_one_contract_with_one_writer(tmp_path: Path) -> None:
    # `release.yml` names artifacts and the updater recognizes them by name, so
    # the packaging writer must produce exactly what the updater looks for.
    archive = make_archive(tmp_path)
    assert archive.name == release_archive_name(NEXT, release_platform_tag())
    assert release_archive_name("1.2.3", "windows-x64") == "swe-mux-1.2.3-windows-x64.zip"
    assert release_archive_name("1.2.3", "linux-x64") == "swe-mux-1.2.3-linux-x64.tar.gz"
    assert release_archive_name("1.2.3", "macos-arm64") == "swe-mux-1.2.3-macos-arm64.tar.gz"


def test_packaging_refuses_a_bundle_that_describes_no_supervisor_protocol(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "swe-mux"
    bundle.mkdir()
    (bundle / "swe-mux.exe").write_bytes(b"MZ")
    with pytest.raises(SystemExit):
        package_desktop_release.build_archive(bundle, tmp_path / "out")


# --- install kind and the supervisor reading ----------------------------------


def test_a_source_run_is_a_source_install_even_beside_a_built_bundle() -> None:
    # The trap `frozen-app-detection-asset-hash` records: a repository can hold a
    # built `dist/` while the daemon reading this runs from source, and swapping
    # that bundle would update an application that is not the running one.
    kind = detect_install_kind(frozen=False, executable="/checkout/.venv/bin/python")
    assert kind.kind == INSTALL_SOURCE
    assert kind.swappable is False
    assert "uv tool upgrade" in kind.upgrade_command


def test_a_frozen_run_names_the_bundle_it_lives_in(tmp_path: Path) -> None:
    exe = tmp_path / "dist" / "swe-mux" / "swe-mux.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    kind = detect_install_kind(frozen=True, executable=str(exe))
    assert kind.kind == INSTALL_FROZEN
    assert kind.swappable is True
    assert kind.bundle_root == exe.parent


def test_the_supervisor_protocol_is_read_from_the_discovery_file(tmp_path: Path) -> None:
    assert running_supervisor_protocol(tmp_path) == (None, REASON_NO_SUPERVISOR)
    write_supervisor(tmp_path, protocol=3)
    assert running_supervisor_protocol(tmp_path) == (3, "ok")
    (tmp_path / "supervisor.json").write_text("{not json", encoding="utf-8")
    assert running_supervisor_protocol(tmp_path) == (None, REASON_SUPERVISOR_UNKNOWN)


# --- durability ----------------------------------------------------------------


async def test_a_restart_during_a_download_reports_an_abandoned_transfer(
    tmp_path: Path,
) -> None:
    # The daemon does not survive its own swap, so "what happened" has to be
    # answerable from disk. A phase left mid-flight must not read as still
    # running - nothing is transferring, because that process is gone.
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "update-install.json").write_text(
        json.dumps({"schema": 1, "phase": "downloading", "version": NEXT, "bytes_downloaded": 17}),
        encoding="utf-8",
    )
    installer = UpdateInstaller(Config(data_dir=data_dir), current_version=CURRENT)
    await installer.ensure_loaded()
    snapshot = installer.snapshot()
    assert snapshot["phase"] == "failed"
    assert "abandoned" in snapshot["message"]


async def test_the_state_file_records_every_phase_of_an_attempt(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))
    await run_install(installer)

    payload = json.loads(
        (Path(installer._config.data_dir) / "update-install.json").read_text(encoding="utf-8")
    )
    assert payload["schema"] == 1
    assert payload["phase"] == PHASE_HANDED_OFF
    assert payload["version"] == NEXT
    assert payload["install_id"]
    phases = [event["phase"] for event in payload["events"]]
    assert phases[0] == "downloading"
    assert phases[-1] == PHASE_HANDED_OFF
    assert "verifying" in phases and "inspecting" in phases


async def test_a_corrupt_state_file_starts_from_empty(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "update-install.json").write_text("{not json", encoding="utf-8")
    installer = UpdateInstaller(Config(data_dir=data_dir))
    await installer.ensure_loaded()
    assert installer.snapshot()["phase"] == "idle"


# --- the other end: what the redeploy script does with the archive -------------
#
# The daemon verified the download, and the script re-verifies it. That is not
# belt-and-braces for its own sake: the script is separately invocable with any
# path a person can type, and a guarantee that holds only when you were called by
# the right process is not a guarantee.


def redeploy_module() -> Any:
    import importlib.util

    from swe_mux import redeploy_launch

    root = redeploy_launch.PACKAGE_DIR.parents[1]
    sys.path.insert(0, str(root / "packaging"))
    try:
        spec = importlib.util.spec_from_file_location(
            "redeploy_desktop_from_archive", root / "packaging" / "redeploy_desktop.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class FakeOutcome:
    def __init__(self) -> None:
        self.records: list[tuple[str, int]] = []

    def record(self, kind: str, detail: str, *, code: int) -> None:
        self.records.append((kind, code))


def test_the_script_refuses_an_archive_whose_hash_it_was_given_and_does_not_match(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = redeploy_module()
    monkeypatch.setattr(module, "STAGING_ROOT", tmp_path / "staging")
    monkeypatch.setattr(module, "log", lambda _message: None)
    archive = make_archive(tmp_path)
    args = SimpleArgs(from_archive=archive, archive_sha256="0" * 64)
    outcome = FakeOutcome()

    assert module.stage_from_archive(args, outcome) == 2
    assert outcome.records == [("refused", 2)]
    assert not (tmp_path / "staging").exists()


def test_the_script_extracts_a_verified_archive_into_the_staging_tree(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = redeploy_module()
    staging = tmp_path / "staging"
    monkeypatch.setattr(module, "STAGING_ROOT", staging)
    monkeypatch.setattr(module, "log", lambda _message: None)
    archive = make_archive(tmp_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    outcome = FakeOutcome()

    assert module.stage_from_archive(SimpleArgs(archive, digest), outcome) == 0
    assert outcome.records == []
    assert (staging / "swe-mux" / "swe-mux.exe").is_file()
    assert (staging / "swe-mux" / "bundle.json").is_file()


def test_the_script_refuses_an_archive_that_is_not_there(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = redeploy_module()
    monkeypatch.setattr(module, "STAGING_ROOT", tmp_path / "staging")
    monkeypatch.setattr(module, "log", lambda _message: None)
    outcome = FakeOutcome()
    assert module.stage_from_archive(SimpleArgs(tmp_path / "nope.zip", ""), outcome) == 2
    assert outcome.records == [("refused", 2)]


class SimpleArgs:
    """The two fields `stage_from_archive` reads off the parsed arguments."""

    def __init__(self, from_archive: Path, archive_sha256: str = "") -> None:
        self.from_archive = from_archive
        self.archive_sha256 = archive_sha256


# --- the route -----------------------------------------------------------------


def route_app(installer: UpdateInstaller | None) -> web.Application:
    app = web.Application()
    if installer is not None:
        app[keys.UPDATE_INSTALL] = installer
    app.add_routes(update_routes.ROUTES)
    return app


async def test_installing_requires_an_explicit_user_action_and_a_named_version(
    tmp_path: Path,
) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))
    client = TestClient(TestServer(route_app(installer)))
    await client.start_server()
    try:
        no_gesture = await client.post("/api/update/install", json={"version": NEXT})
        assert no_gesture.status == 400
        assert fetch.calls == []

        no_version = await client.post(
            "/api/update/install",
            json={},
            headers={"X-Mux-User-Gesture": "update-install"},
        )
        assert no_version.status == 400
        assert (await no_version.json())["error"] == "version_required"
        assert fetch.calls == []

        accepted = await client.post(
            "/api/update/install",
            json={"version": NEXT},
            headers={"X-Mux-User-Gesture": "update-install"},
        )
        assert accepted.status == 202
        assert (await accepted.json())["phase"] == "downloading"
        await installer.wait()
        assert installer.snapshot()["phase"] == PHASE_HANDED_OFF
    finally:
        await client.close()


async def test_a_source_install_is_refused_over_the_route_with_the_command_to_run(
    tmp_path: Path,
) -> None:
    fetch = FakeFetch({MANIFEST_URL: manifest()})
    installer = build(tmp_path, fetch=fetch, frozen=False)
    client = TestClient(TestServer(route_app(installer)))
    await client.start_server()
    try:
        response = await client.post(
            "/api/update/install",
            json={"version": NEXT},
            headers={"X-Mux-User-Gesture": "update-install"},
        )
        assert response.status == 409
        payload = await response.json()
        assert payload["error"] == REASON_SOURCE_INSTALL
        assert "uv tool upgrade swe-mux" in payload["message"]
        assert payload["install_kind"] == INSTALL_SOURCE
    finally:
        await client.close()


async def test_reading_the_install_endpoint_never_reaches_the_network(
    tmp_path: Path,
) -> None:
    fetch = FakeFetch({MANIFEST_URL: manifest()})
    installer = build(tmp_path, fetch=fetch)
    client = TestClient(TestServer(route_app(installer)))
    await client.start_server()
    try:
        for _ in range(3):
            response = await client.get("/api/update/install")
            assert response.status == 200
            assert (await response.json())["phase"] == "idle"
        assert fetch.calls == []
    finally:
        await client.close()


async def test_a_daemon_without_an_installer_answers_quietly() -> None:
    client = TestClient(TestServer(route_app(None)))
    await client.start_server()
    try:
        read = await client.get("/api/update/install")
        assert read.status == 200
        assert (await read.json())["swappable"] is False
        written = await client.post(
            "/api/update/install",
            json={"version": NEXT},
            headers={"X-Mux-User-Gesture": "update-install"},
        )
        assert written.status == 200
        assert (await written.json())["swappable"] is False
    finally:
        await client.close()
