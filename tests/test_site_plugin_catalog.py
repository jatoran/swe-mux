from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_catalog_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "site_plugin_catalog", REPO_ROOT / "site" / "tools" / "plugins.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CATALOG = load_catalog_module()


def manifest(*, plugin_id: str, version: str = "0.1.0") -> bytes:
    return f'''manifest_version = 1
id = "{plugin_id}"
name = "Catalog fixture"
version = "{version}"
min_swe_mux_version = "0.2.0"
description = "A valid fixture."
author = "swe-mux"
license = "MIT"
platforms = ["windows", "linux", "macos"]
requires = ["plugin.actions.v1"]
permissions = ["projects.read"]
runtime_requirements = ["python>=3.10"]

[[actions]]
id = "inspect"
title = "Inspect"
contexts = ["project"]
command = ["python", "plugin.py"]
'''.encode()


class FakeGitHub:
    def __init__(self, raw_manifest: bytes) -> None:
        self.raw_manifest = raw_manifest

    def get(self, path: str, *, allow_missing: bool = False) -> Any:
        if "/commits/main" in path:
            return {"sha": "a" * 40}
        if "/commits/v0.1.0" in path:
            return {"sha": "b" * 40}
        if "/contents/swe-mux-plugin.toml" in path:
            return {
                "encoding": "base64",
                "content": base64.encodebytes(self.raw_manifest).decode(),
            }
        if "/releases/latest" in path:
            return {
                "tag_name": "v0.1.0",
                "html_url": "https://github.com/jatoran/example/releases/tag/v0.1.0",
                "published_at": "2026-08-31T00:00:00Z",
            }
        raise AssertionError(path)


def repository(full_name: str) -> dict[str, Any]:
    owner, name = full_name.split("/", 1)
    return {
        "name": name,
        "full_name": full_name,
        "owner": {"login": owner},
        "default_branch": "main",
        "description": "Repository description",
        "stargazers_count": 4,
        "language": "Python",
        "updated_at": "2026-08-31T00:00:00Z",
        "html_url": f"https://github.com/{full_name}",
        "license": {"spdx_id": "MIT"},
        "fork": False,
        "archived": False,
    }


def test_catalog_validates_and_indexes_an_official_release() -> None:
    full_name = "jatoran/swe-mux-plugin-fleet-dashboard"
    listing = CATALOG.build_listing(
        FakeGitHub(manifest(plugin_id="swemux.official.fleet-dashboard")),
        repository(full_name),
    )
    assert listing["official"] is True
    assert listing["install_ref"] == "v0.1.0"
    assert listing["indexed_ref"] == "b" * 40
    assert listing["manifest"]["contributions"]["actions"] == 1


def test_catalog_rejects_release_and_manifest_version_drift() -> None:
    with pytest.raises(CATALOG.CatalogError, match="tag does not match"):
        CATALOG.build_listing(
            FakeGitHub(
                manifest(plugin_id="swemux.official.fleet-dashboard", version="0.2.0")
            ),
            repository("jatoran/swe-mux-plugin-fleet-dashboard"),
        )


def test_catalog_rejects_an_unnamespaced_plugin_id() -> None:
    with pytest.raises(CATALOG.CatalogError, match="globally namespaced"):
        CATALOG.validate_manifest(manifest(plugin_id="x"))
