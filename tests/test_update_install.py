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
from swe_mux import bundle_apply
from swe_mux.bundle_archive import (
    ARCHIVE_ROOT,
    CLI_ROOT,
    SUPERVISOR_ROOT,
    TAR_GZ_SUFFIX,
    ArchiveError,
    archive_bundle_names,
    read_archive_metadata,
    validate_members,
)
from swe_mux.bundle_manifest import DELTA_NO_MANIFEST
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
    CONSENT_SUPERVISOR_UPDATE,
    INSTALL_FROZEN,
    INSTALL_SOURCE,
    MANAGED_CHECKOUT,
    MANAGED_INSTALLER,
    MANAGED_PORTABLE,
    PHASE_HANDED_OFF,
    PHASE_REFUSED,
    REASON_ARCHIVE_INVALID,
    REASON_BUNDLE_METADATA_MISSING,
    REASON_HASH_MISMATCH,
    REASON_MALFORMED,
    REASON_NO_APPLIER,
    REASON_NO_ARTIFACT,
    REASON_NO_SUPERVISOR,
    REASON_SOURCE_INSTALL,
    REASON_SUPERVISOR_IN_BUNDLE,
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
    applier_executable_name,
    detect_install_kind,
    prepare_applier,
    release_archive_name,
    release_bundle_metadata_name,
    release_file_manifest_name,
    release_installer_name,
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
    """Stands in for spawning the applier. Records what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, str]] = []

    def __call__(self, archive: Path, version: str, mode: str) -> int:
        self.calls.append((archive, version, mode))
        return 4242


def make_bundle(
    directory: Path,
    *,
    version: str = NEXT,
    protocol: int = SUPERVISOR_PROTOCOL,
    siblings: bool = True,
) -> Path:
    """A directory shaped like a built bundle, small enough to zip in a test.

    `siblings` lays the console client and the supervisor bundle out beside it,
    the way `build_desktop.py` and the installer do - the client is what the
    updater runs the swap from, so an archive without one is the pre-2026-09-05
    shape and the tests that want that say so.
    """
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
    if siblings:
        cli = directory.parent / CLI_ROOT
        cli.mkdir(parents=True, exist_ok=True)
        (cli / applier_executable_name()).write_bytes(b"MZ not really a client")
        supervisor = directory.parent / SUPERVISOR_ROOT
        supervisor.mkdir(parents=True, exist_ok=True)
        (supervisor / "swe-mux-supervisor.exe").write_bytes(b"MZ not really a supervisor")
    return directory


def make_archive(
    tmp_path: Path,
    *,
    version: str = NEXT,
    protocol: int = SUPERVISOR_PROTOCOL,
    siblings: bool = True,
) -> Path:
    """A real release archive, produced by the real packaging writer."""
    bundle = make_bundle(
        tmp_path / "build" / "swe-mux", version=version, protocol=protocol, siblings=siblings
    )
    return package_desktop_release.build_archive(
        bundle,
        tmp_path / "out",
        siblings=package_desktop_release.sibling_bundles(bundle) if siblings else None,
    )[0]


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
def no_redeploy_in_flight(monkeypatch: Any) -> None:
    """Pretend no redeploy lock is held.

    Stubbed rather than relied on: the preflight's answer would otherwise depend
    on whether the machine running the suite has a live `redeploy.lock` in the
    fixture's data dir, which it never does, but saying so is cheaper than
    reasoning about it in every test.
    """
    monkeypatch.setattr("swe_mux.update_install.redeploy_lock_pid", lambda _config: None)


def build(
    tmp_path: Path,
    *,
    fetch: FakeFetch,
    download: FakeDownload | None = None,
    handoff: Handoff | None = None,
    frozen: bool = True,
    managed: str = MANAGED_PORTABLE,
    supervisor_exe: Path | None = None,
) -> UpdateInstaller:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    kind = (
        InstallKind(
            kind=INSTALL_FROZEN,
            bundle_root=tmp_path / "dist" / "swe-mux",
            upgrade_command="",
            managed=managed,
            install_root=tmp_path / "dist",
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
        # The running supervisor's image: outside the app bundle unless a test
        # puts it inside to exercise the `--supervisor-child` fallback case.
        supervisor_exe=lambda: supervisor_exe or tmp_path / "dist" / SUPERVISOR_ROOT / "s.exe",
    )


async def run_install(
    installer: UpdateInstaller, version: str = NEXT, *, accept: bool = False
) -> dict[str, Any]:
    """Start an install and let it finish, returning the final snapshot."""
    await installer.start(version, accept_supervisor_update=accept)
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
    # fleet. So it stops here - and says what accepting would cost, because the
    # same request with consent is what proceeds.
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
    assert snapshot["consent"] == CONSENT_SUPERVISOR_UPDATE
    assert "ends every live terminal session" in snapshot["message"]
    assert handoff.calls == []
    # The verified archive is kept: the operator may still consent, and
    # re-downloading hundreds of megabytes to do so would be a waste.
    assert (installer.downloads_dir / archive.name).is_file()


async def test_consent_installs_the_supervisor_release_in_replace_mode(tmp_path: Path) -> None:
    # The other half of the gate. Consent turns the refusal into a replace-mode
    # handoff - the applier stops with quit intent and swaps the supervisor
    # bundle too - and the archive verified by the refused attempt is reused.
    archive = make_archive(tmp_path, protocol=SUPERVISOR_PROTOCOL + 1)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    download = FakeDownload(archive.read_bytes())
    installer = build(tmp_path, fetch=fetch, download=download, handoff=handoff)
    write_supervisor(Path(installer._config.data_dir), protocol=SUPERVISOR_PROTOCOL)
    assert (await run_install(installer))["reason"] == REASON_SUPERVISOR_UPDATE_REQUIRED

    snapshot = await run_install(installer, accept=True)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert snapshot["mode"] == bundle_apply.MODE_REPLACE
    assert "Every live session ends" in snapshot["message"]
    assert handoff.calls == [(installer.downloads_dir / archive.name, NEXT, "replace")]
    assert len(download.calls) == 1


async def test_consent_is_permission_not_an_instruction(tmp_path: Path) -> None:
    # A release that can be installed around the sessions is, whatever the flag
    # says: the operator accepted a reap *if needed*, not a reap.
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    installer = build(
        tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()), handoff=handoff
    )
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer, accept=True)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert snapshot["mode"] == bundle_apply.MODE_SWAP
    assert handoff.calls[0][2] == "swap"


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
    assert snapshot["consent"] == CONSENT_SUPERVISOR_UPDATE


async def test_no_running_supervisor_refuses_because_a_swap_would_reap_sessions(
    tmp_path: Path,
) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))

    snapshot = await run_install(installer)

    assert snapshot["reason"] == REASON_NO_SUPERVISOR
    assert snapshot["consent"] == CONSENT_SUPERVISOR_UPDATE


async def test_a_supervisor_running_inside_the_app_bundle_needs_consent(
    tmp_path: Path,
) -> None:
    # The `--supervisor-child` fallback: the supervisor shares the app's image,
    # so renaming the bundle kills it. Not a protocol question, and not one a
    # matching protocol number can answer - which is why it is its own word.
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    installer = build(
        tmp_path,
        fetch=fetch,
        download=FakeDownload(archive.read_bytes()),
        handoff=handoff,
        supervisor_exe=tmp_path / "dist" / "swe-mux" / "swe-mux.exe",
    )
    write_supervisor(Path(installer._config.data_dir))

    refused = await run_install(installer)
    assert refused["reason"] == REASON_SUPERVISOR_IN_BUNDLE
    assert refused["consent"] == CONSENT_SUPERVISOR_UPDATE
    assert handoff.calls == []

    accepted = await run_install(installer, accept=True)
    assert accepted["phase"] == PHASE_HANDED_OFF
    assert accepted["mode"] == bundle_apply.MODE_REPLACE


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
    # ...and still recorded, so `swemux update` can say why nothing happened.
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
    handed, version, mode = handoff.calls[0]
    assert version == NEXT
    assert mode == bundle_apply.MODE_SWAP
    assert handed == installer.downloads_dir / archive.name
    # What was handed over is byte-identical to what the manifest hashed.
    assert handed.read_bytes() == archive.read_bytes()
    # The applier came out of the archive, under the data dir - never inside a
    # tree the swap renames - and it is the release's own client.
    applier = Path(snapshot["applier"])
    assert applier.is_file()
    assert applier.name == applier_executable_name()
    assert applier.is_relative_to(installer.downloads_dir)
    assert not applier.is_relative_to(tmp_path / "dist")


async def test_an_archive_without_a_client_falls_back_to_the_installed_one(
    tmp_path: Path,
) -> None:
    # Every archive published before 2026-09-05 carries only `swe-mux/`. The
    # client already installed beside the app performs the swap for those.
    archive = make_archive(tmp_path, siblings=False)
    assert archive_bundle_names(archive) == (ARCHIVE_ROOT,)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))
    installed_cli = tmp_path / "dist" / CLI_ROOT
    installed_cli.mkdir(parents=True)
    (installed_cli / applier_executable_name()).write_bytes(b"MZ the installed client")

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert Path(snapshot["applier"]).read_bytes() == b"MZ the installed client"


async def test_no_client_anywhere_is_a_refusal_that_names_the_manual_path(
    tmp_path: Path,
) -> None:
    # A portable archive unpacked without its siblings, updating to an old
    # release: nothing on the machine can run the swap from outside the app.
    archive = make_archive(tmp_path, siblings=False)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    handoff = Handoff()
    installer = build(
        tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()), handoff=handoff
    )
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_REFUSED
    assert snapshot["reason"] == REASON_NO_APPLIER
    assert "by hand" in snapshot["message"]
    assert handoff.calls == []


def test_prepare_applier_prefers_the_archive_and_reports_the_shape(tmp_path: Path) -> None:
    archive = make_archive(tmp_path)
    layout = bundle_apply.Layout(tmp_path / "dist")
    launcher = prepare_applier(archive, tmp_path / "applier", layout)
    assert launcher == tmp_path / "applier" / CLI_ROOT / applier_executable_name()
    assert launcher is not None and launcher.read_bytes() == b"MZ not really a client"
    # Re-preparing into the same directory starts from empty rather than merging.
    (tmp_path / "applier" / "stale.txt").write_text("old", encoding="utf-8")
    prepare_applier(archive, tmp_path / "applier", layout)
    assert not (tmp_path / "applier" / "stale.txt").exists()
    # Nothing to prepare from answers None rather than a launcher that is not there.
    bare = make_archive(tmp_path / "bare", siblings=False)
    empty = bundle_apply.Layout(tmp_path / "x")
    assert prepare_applier(bare, tmp_path / "applier-2", empty) is None
    assert not (tmp_path / "applier-2").exists()


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
    assert kind.managed == ""
    # The command is derived from how *this* copy was installed - the suite
    # runs from a checkout, and a checkout is told to pull rather than to run a
    # tool command it has no tool for.
    assert kind.upgrade_command
    assert "swe-mux" in kind.upgrade_command or "uv sync" in kind.upgrade_command


def test_a_frozen_run_names_the_bundle_it_lives_in(tmp_path: Path, monkeypatch: Any) -> None:
    exe = tmp_path / "dist" / "swe-mux" / "swe-mux.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    kind = detect_install_kind(frozen=True, executable=str(exe))
    assert kind.kind == INSTALL_FROZEN
    assert kind.swappable is True
    assert kind.bundle_root == exe.parent
    assert kind.install_root == exe.parent.parent
    assert kind.layout is not None and kind.layout.app == exe.parent
    # Run from the suite, the package sits in a checkout, which is what the
    # frozen app on a developer machine looks like from inside the bundle.
    assert kind.managed == MANAGED_CHECKOUT


def test_a_frozen_run_tells_an_installer_install_from_a_portable_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The three frozen shapes swap identically; what the answer decides is
    # whether the installer's Add/Remove Programs entry is brought up to date
    # afterwards. Read from the registry rather than guessed from a path.
    monkeypatch.setattr("swe_mux.update_install.redeploy_source_root", lambda: None)
    exe = tmp_path / "Programs" / "swe-mux" / "swe-mux" / "swe-mux.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    install_root = exe.parent.parent

    def registered_here(_key: str, name: str) -> str | None:
        return {"InstallLocation": str(install_root), "DisplayVersion": "0.2.2"}.get(name)

    def registered_elsewhere(_key: str, name: str) -> str | None:
        return {"InstallLocation": str(tmp_path / "other")}.get(name)

    def unregistered(_key: str, _name: str) -> str | None:
        return None

    if sys.platform != "win32":
        # The registry is a Windows thing; off it the reader is never consulted
        # and every frozen copy outside a checkout is portable.
        kind = detect_install_kind(
            frozen=True, executable=str(exe), registration_reader=registered_here
        )
        assert kind.managed == MANAGED_PORTABLE
        return
    kind = detect_install_kind(
        frozen=True, executable=str(exe), registration_reader=registered_here
    )
    assert kind.managed == MANAGED_INSTALLER
    assert kind.install_root == install_root
    kind = detect_install_kind(
        frozen=True, executable=str(exe), registration_reader=registered_elsewhere
    )
    assert kind.managed == MANAGED_PORTABLE
    kind = detect_install_kind(frozen=True, executable=str(exe), registration_reader=unregistered)
    assert kind.managed == MANAGED_PORTABLE


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
    assert payload["schema"] == 2
    assert payload["phase"] == PHASE_HANDED_OFF
    assert payload["version"] == NEXT
    assert payload["install_id"]
    assert payload["mode"] == bundle_apply.MODE_SWAP
    phases = [event["phase"] for event in payload["events"]]
    assert phases[0] == "downloading"
    assert phases[-1] == PHASE_HANDED_OFF
    assert "verifying" in phases and "inspecting" in phases and "preparing" in phases


async def test_a_corrupt_state_file_starts_from_empty(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "update-install.json").write_text("{not json", encoding="utf-8")
    installer = UpdateInstaller(Config(data_dir=data_dir))
    await installer.ensure_loaded()
    assert installer.snapshot()["phase"] == "idle"


# --- the other end: what the applier does with the archive ----------------------
#
# The daemon verified the download, and the applier re-verifies it. That is not
# belt-and-braces for its own sake: `bundle_apply` is separately invocable with
# any path a person can type (`swemux update-apply`, `redeploy_desktop.py
# --from-archive`), and a guarantee that holds only when you were called by the
# right process is not a guarantee.


class FakeOutcome:
    def __init__(self) -> None:
        self.records: list[tuple[str, int]] = []
        self.facts: dict[str, Any] = {}

    def record(self, kind: str, detail: str, *, code: int) -> None:
        self.records.append((kind, code))

    def describe(self, **facts: Any) -> None:
        self.facts.update(facts)


@pytest.fixture(autouse=True)
def quiet_applier(monkeypatch: Any) -> None:
    """The applier prints progress; a test suite does not want it on stdout."""
    monkeypatch.setattr(bundle_apply, "log", lambda _message: None)


def test_the_applier_stages_a_delta_against_the_installed_bundle(tmp_path: Path) -> None:
    # The other half of the updater's win, in the process that actually performs
    # it. The daemon's preview is advisory; this is the code that decides what
    # gets written, and it decides from the manifest *inside* the archive - the
    # copy the whole-archive digest it just checked already covers.
    layout = bundle_apply.Layout(tmp_path / "installed")
    installed = make_bundle(layout.app, version=CURRENT)
    (installed / "_internal" / "heavy.bin").write_bytes(b"H" * 200_000)
    bundle = make_bundle(tmp_path / "build" / "swe-mux", version=NEXT)
    (bundle / "_internal" / "heavy.bin").write_bytes(b"H" * 200_000)
    archive, _ = package_desktop_release.build_archive(
        bundle, tmp_path / "out", siblings=package_desktop_release.sibling_bundles(bundle)
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    outcome = FakeOutcome()

    code, result, metadata = bundle_apply.stage_from_archive(archive, digest, layout, outcome)

    assert code == 0 and result is not None and metadata is not None
    assert metadata.version == NEXT
    assert outcome.records == []
    staged = layout.staged(bundle_apply.APP_BUNDLE)
    assert (staged / "swe-mux.exe").is_file()
    # The 200 KB standing in for the machine-learning closure was not rewritten;
    # it is the same filesystem object, which is what keeps its scan verdict.
    assert (staged / "_internal" / "heavy.bin").read_bytes() == b"H" * 200_000
    assert (staged / "_internal" / "heavy.bin").samefile(installed / "_internal" / "heavy.bin")
    # The siblings arrive beside it, whole, under the names the swap moves.
    assert result.bundles == (ARCHIVE_ROOT, SUPERVISOR_ROOT, CLI_ROOT)
    assert (layout.staged(CLI_ROOT) / applier_executable_name()).is_file()
    assert (layout.staged(SUPERVISOR_ROOT) / "swe-mux-supervisor.exe").is_file()


def test_the_applier_refuses_an_archive_whose_hash_it_was_given_and_does_not_match(
    tmp_path: Path,
) -> None:
    layout = bundle_apply.Layout(tmp_path / "installed")
    archive = make_archive(tmp_path)
    outcome = FakeOutcome()

    code, result, _ = bundle_apply.stage_from_archive(archive, "0" * 64, layout, outcome)

    assert code == 2 and result is None
    assert outcome.records == [("refused", 2)]
    assert not layout.staging_root.exists()


def test_the_applier_extracts_a_verified_archive_into_the_staging_tree(
    tmp_path: Path,
) -> None:
    layout = bundle_apply.Layout(tmp_path / "installed")
    archive = make_archive(tmp_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    outcome = FakeOutcome()

    code, result, _ = bundle_apply.stage_from_archive(archive, digest, layout, outcome)

    assert code == 0 and result is not None
    assert outcome.records == []
    assert (layout.staged("swe-mux") / "swe-mux.exe").is_file()
    assert (layout.staged("swe-mux") / "bundle.json").is_file()


def test_the_applier_refuses_an_archive_that_is_not_there(tmp_path: Path) -> None:
    layout = bundle_apply.Layout(tmp_path / "installed")
    outcome = FakeOutcome()
    code, _, _ = bundle_apply.stage_from_archive(tmp_path / "nope.zip", "", layout, outcome)
    assert code == 2
    assert outcome.records == [("refused", 2)]


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


# --- the delta preview --------------------------------------------------------
#
# What the install says about itself *before* it commits to several hundred
# megabytes. Every test here is also a test that the preview cannot break an
# install: it is advisory, the swap recomputes the same plan authoritatively from
# the copy inside the archive, and every failure mode below still hands off.


class FakeDownloads:
    """A downloader that answers per URL, so a release can publish two files."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
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
        payload = self.payloads.get(url)
        if payload is None:
            return DownloadOutcome(status=404, declared_bytes=None, received_bytes=0)
        write(payload)
        return DownloadOutcome(status=200, declared_bytes=len(payload), received_bytes=len(payload))


#: The stand-in for the ~370 MB of machine-learning dependencies that dominate a
#: real bundle and move on nobody's release schedule. It has to be bulky, because
#: the fallback is decided on the *share of bytes* already present and a bundle
#: made only of small files is one where a delta genuinely is not worth doing.
HEAVY = {"_internal/heavy.bin": b"H" * 200_000}


def make_release(
    tmp_path: Path, *, version: str = NEXT, extra: dict[str, bytes] | None = None
) -> tuple[Path, Path]:
    """A real archive and its real sidecar manifest, from the real writer."""
    bundle = make_bundle(tmp_path / "build" / "swe-mux", version=version)
    for name, payload in (extra or {}).items():
        (bundle / name).write_bytes(payload)
    return package_desktop_release.build_archive(
        bundle, tmp_path / "out", siblings=package_desktop_release.sibling_bundles(bundle)
    )


async def test_the_install_reports_how_much_of_the_release_is_already_here(
    tmp_path: Path,
) -> None:
    archive, sidecar = make_release(tmp_path, extra=HEAVY)
    # An installed bundle identical to the release except for its executable:
    # the shape of every ordinary update, where the interpreter and the
    # dependencies are already on the machine and only our own code moved.
    installed = make_bundle(tmp_path / "dist" / "swe-mux", version=CURRENT)
    for name, payload in HEAVY.items():
        (installed / name).write_bytes(payload)
    (installed / "swe-mux.exe").write_bytes(b"MZ an older executable")
    fetch = FakeFetch(
        {MANIFEST_URL: manifest(artifacts=[artifact_entry(archive), artifact_entry(sidecar)])}
    )
    download = FakeDownloads(
        {
            artifact_entry(archive)["url"]: archive.read_bytes(),
            artifact_entry(sidecar)["url"]: sidecar.read_bytes(),
        }
    )
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    delta = snapshot["delta"]
    assert delta["eligible"] is True
    # `base_library.zip` and `heavy.bin` are unchanged; `swe-mux.exe` and
    # `bundle.json` both moved, so two files are written and two are kept.
    assert delta["reuse_files"] == 2
    assert delta["write_files"] == 2
    assert delta["reuse_bytes"] > 0
    # ...and it is durable, because the daemon does not survive the swap.
    stored = json.loads(
        (Path(installer._config.data_dir) / "update-install.json").read_text("utf-8")
    )
    assert stored["delta"]["eligible"] is True


async def test_a_release_that_publishes_no_file_manifest_still_installs(
    tmp_path: Path,
) -> None:
    # Every release published before the manifest existed is this case, and it
    # has to install exactly the way it always did rather than being refused for
    # missing a file it never promised.
    archive, _ = make_release(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert snapshot["delta"]["reason"] == DELTA_NO_MANIFEST
    assert snapshot["delta"]["eligible"] is False


async def test_a_sidecar_manifest_that_fails_its_hash_is_not_used_and_stops_nothing(
    tmp_path: Path,
) -> None:
    # The preview is advisory, so a bad sidecar must neither be believed nor
    # allowed to refuse an install whose *archive* verifies. It is the archive's
    # own copy that the swap acts on, and that one is covered by the whole-archive
    # digest this install already checked.
    archive, sidecar = make_release(tmp_path)
    make_bundle(tmp_path / "dist" / "swe-mux", version=CURRENT)
    entries = [artifact_entry(archive), artifact_entry(sidecar, sha256="f" * 64)]
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=entries)})
    download = FakeDownloads(
        {entries[0]["url"]: archive.read_bytes(), entries[1]["url"]: sidecar.read_bytes()}
    )
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir))

    snapshot = await run_install(installer)

    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert snapshot["delta"]["eligible"] is False
    assert snapshot["delta"]["reason"] == DELTA_NO_MANIFEST


async def test_the_sidecar_manifest_name_cannot_collide_with_the_archives(
    tmp_path: Path,
) -> None:
    # The updater looks its own artifact up by *exact* name, so a third name on
    # the same contract has to be unmistakably not the other two under any
    # version string.
    version = "1.2.3"
    names = {
        release_archive_name(version, "windows-x64"),
        release_file_manifest_name(version, "windows-x64"),
        release_bundle_metadata_name(version, "windows-x64"),
        release_installer_name(version, "windows-x64"),
    }
    assert len(names) == 4
    archive, sidecar = make_release(tmp_path)
    assert sidecar.name == release_file_manifest_name(NEXT, release_platform_tag())
    assert sidecar.name != archive.name
    # The metadata sidecar is the bundle's own `bundle.json`, byte for byte, so
    # the plan reads exactly what the install will re-read out of the archive.
    described = sidecar.with_name(release_bundle_metadata_name(NEXT, release_platform_tag()))
    assert described.is_file()
    assert described.read_bytes() == (tmp_path / "build" / "swe-mux" / "bundle.json").read_bytes()


# --- the plan: what the press would do, said before it ---------------------------


def release_with_sidecars(
    tmp_path: Path, *, protocol: int = SUPERVISOR_PROTOCOL
) -> tuple[dict[str, Any], FakeDownloads, Path]:
    """A manifest naming the archive and both sidecars, and a downloader for all three."""
    bundle = make_bundle(tmp_path / "build" / "swe-mux", version=NEXT, protocol=protocol)
    archive, files = package_desktop_release.build_archive(
        bundle, tmp_path / "out", siblings=package_desktop_release.sibling_bundles(bundle)
    )
    described = files.with_name(release_bundle_metadata_name(NEXT, release_platform_tag()))
    entries = [artifact_entry(archive), artifact_entry(files), artifact_entry(described)]
    payloads = {
        entries[0]["url"]: archive.read_bytes(),
        entries[1]["url"]: files.read_bytes(),
        entries[2]["url"]: described.read_bytes(),
    }
    return manifest(artifacts=entries), FakeDownloads(payloads), archive


async def test_the_plan_says_before_the_download_whether_sessions_survive(
    tmp_path: Path,
) -> None:
    # The question the operator has to answer before anything is fetched, and
    # the reason the metadata sidecar exists: a few hundred bytes, read instead
    # of a few hundred megabytes.
    manifest_payload, download, archive = release_with_sidecars(
        tmp_path, protocol=SUPERVISOR_PROTOCOL + 1
    )
    fetch = FakeFetch({MANIFEST_URL: manifest_payload})
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir), protocol=SUPERVISOR_PROTOCOL)

    plan = await installer.plan(NEXT)

    assert plan["version"] == NEXT
    assert plan["mode"] == bundle_apply.MODE_REPLACE
    assert plan["reaps_sessions"] is True
    assert plan["consent"] == CONSENT_SUPERVISOR_UPDATE
    assert plan["consent_reason"] == REASON_SUPERVISOR_UPDATE_REQUIRED
    assert plan["supervisor"]["known"] is True
    assert plan["supervisor"]["incoming_protocol"] == SUPERVISOR_PROTOCOL + 1
    assert plan["supervisor"]["running_protocol"] == SUPERVISOR_PROTOCOL
    assert plan["archive_cached"] is False
    # The archive itself was never fetched: only the manifest and the sidecars.
    assert archive.name not in " ".join(download.calls)
    # And a plan is a question, not an attempt: the attempt state is untouched.
    assert installer.snapshot()["phase"] == "idle"
    assert installer.snapshot()["artifact"] == ""


async def test_the_plan_reports_a_preserving_release_and_the_delta(tmp_path: Path) -> None:
    manifest_payload, download, _ = release_with_sidecars(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest_payload})
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir))
    make_bundle(tmp_path / "dist" / "swe-mux", version=CURRENT)

    plan = await installer.plan(NEXT)

    assert plan["mode"] == bundle_apply.MODE_SWAP
    assert plan["reaps_sessions"] is False
    assert plan["consent"] == ""
    assert "preserved" in plan["supervisor"]["message"]
    assert plan["delta"]["reason"] in {"ok", "too_little_reuse"}
    assert plan["managed"] == MANAGED_PORTABLE


async def test_a_release_without_the_metadata_sidecar_plans_as_unknown(
    tmp_path: Path,
) -> None:
    # Every release before 2026-09-05. The plan does not guess; the install
    # answers from the archive after the download, exactly as it always did.
    archive = make_archive(tmp_path, protocol=SUPERVISOR_PROTOCOL + 1)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))

    plan = await installer.plan(NEXT)

    assert plan["supervisor"]["known"] is False
    assert plan["supervisor"]["incoming_protocol"] is None
    assert plan["mode"] == bundle_apply.MODE_SWAP
    assert plan["consent"] == ""
    snapshot = await run_install(installer)
    assert snapshot["reason"] == REASON_SUPERVISOR_UPDATE_REQUIRED


async def test_the_plan_refuses_a_source_install_with_the_command_to_run(
    tmp_path: Path,
) -> None:
    fetch = FakeFetch({MANIFEST_URL: manifest()})
    installer = build(tmp_path, fetch=fetch, frozen=False)
    with pytest.raises(UpdateRefused) as refusal:
        await installer.plan(NEXT)
    assert refusal.value.reason == REASON_SOURCE_INSTALL
    assert "uv tool upgrade swe-mux" in refusal.value.message
    assert fetch.calls == []


async def test_the_plan_route_needs_its_own_gesture_and_reports_live_sessions(
    tmp_path: Path,
) -> None:
    manifest_payload, download, _ = release_with_sidecars(
        tmp_path, protocol=SUPERVISOR_PROTOCOL + 1
    )
    fetch = FakeFetch({MANIFEST_URL: manifest_payload})
    installer = build(tmp_path, fetch=fetch, download=download)
    write_supervisor(Path(installer._config.data_dir), protocol=SUPERVISOR_PROTOCOL)
    app = route_app(installer)

    class Pty:
        def __init__(self, alive: bool) -> None:
            self._alive = alive

        def isalive(self) -> bool:
            return self._alive

    class Session:
        def __init__(self, alive: bool) -> None:
            self.pty = Pty(alive)

    app[keys.SESSIONS] = type(  # type: ignore[assignment]
        "Sessions", (), {"sessions": {"a": Session(True), "b": Session(True), "c": Session(False)}}
    )()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        wrong_word = await client.post(
            "/api/update/plan",
            json={"version": NEXT},
            headers={"X-Mux-User-Gesture": "update-install"},
        )
        assert wrong_word.status == 400
        assert fetch.calls == []
        response = await client.post(
            "/api/update/plan",
            json={"version": NEXT},
            headers={"X-Mux-User-Gesture": "update-plan"},
        )
        assert response.status == 200
        plan = await response.json()
        assert plan["reaps_sessions"] is True
        assert plan["live_sessions"] == 2
        # Then the install without consent is refused over the route, with the
        # consent word a client needs to know what to ask.
        refused = await client.post(
            "/api/update/install",
            json={"version": NEXT},
            headers={"X-Mux-User-Gesture": "update-install"},
        )
        assert refused.status == 202
        await installer.wait()
        snapshot = installer.snapshot()
        assert snapshot["reason"] == REASON_SUPERVISOR_UPDATE_REQUIRED
        assert snapshot["consent"] == CONSENT_SUPERVISOR_UPDATE
        accepted = await client.post(
            "/api/update/install",
            json={"version": NEXT, "accept_supervisor_update": True},
            headers={"X-Mux-User-Gesture": "update-install"},
        )
        assert accepted.status == 202
        await installer.wait()
        assert installer.snapshot()["phase"] == PHASE_HANDED_OFF
        assert installer.snapshot()["mode"] == bundle_apply.MODE_REPLACE
    finally:
        await client.close()


async def test_the_handoff_tells_every_client_which_kind_of_swap_began(
    tmp_path: Path,
) -> None:
    archive = make_archive(tmp_path)
    fetch = FakeFetch({MANIFEST_URL: manifest(artifacts=[artifact_entry(archive)])})
    installer = build(tmp_path, fetch=fetch, download=FakeDownload(archive.read_bytes()))
    write_supervisor(Path(installer._config.data_dir))
    announced: list[tuple[int, str]] = []

    async def announce(pid: int, mode: str) -> None:
        announced.append((pid, mode))

    installer.announce = announce
    snapshot = await run_install(installer)
    assert snapshot["phase"] == PHASE_HANDED_OFF
    assert announced == [(4242, "swap")]
