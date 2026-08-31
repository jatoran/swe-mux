"""Build the validated public plugin catalog without executing plugin code."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API = "https://api.github.com"
TOPIC_QUERY = "topic:swe-mux-plugin"
MAX_MANIFEST_BYTES = 256 * 1024
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
OFFICIAL = {
    "jatoran/swe-mux-plugin-fleet-dashboard": "swemux.official.fleet-dashboard",
    "jatoran/swe-mux-plugin-project-links": "swemux.official.project-links",
    "jatoran/swe-mux-plugin-session-switchboard": "swemux.official.session-switchboard",
    "jatoran/swe-mux-plugin-worktree-auditor": "swemux.official.worktree-auditor",
}

log = logging.getLogger("plugin-catalog")


class CatalogError(ValueError):
    pass


class GitHub:
    def __init__(self, token: str = "") -> None:
        self.token = token
        self.requests = 0

    def get(self, path: str, *, allow_missing: bool = False) -> Any:
        url = path if path.startswith("https://") else f"{API}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "swe-mux-plugin-catalog/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        self.requests += 1
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if allow_missing and exc.code == 404:
                return None
            raise CatalogError(f"GitHub returned HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CatalogError(f"GitHub request failed for {url}: {exc}") from exc


def _strings(manifest: dict[str, Any], field: str, *, required: bool = False) -> list[str]:
    value = manifest.get(field)
    if value is None and not required:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise CatalogError(f"manifest {field} must be a string array")
    return value


def validate_manifest(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise CatalogError("manifest exceeds 256 KiB")
    try:
        manifest = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CatalogError(f"manifest does not parse: {exc}") from exc
    if manifest.get("manifest_version") != 1:
        raise CatalogError("manifest_version must be 1")
    for field in ("id", "name", "version", "min_swe_mux_version"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise CatalogError(f"manifest {field} is required")
    if not IDENTIFIER.fullmatch(manifest["id"]):
        raise CatalogError("manifest id is not globally namespaced")
    if not VERSION.fullmatch(manifest["version"]):
        raise CatalogError("manifest version is not semantic")
    if not VERSION.fullmatch(manifest["min_swe_mux_version"]):
        raise CatalogError("manifest min_swe_mux_version is not semantic")
    platforms = _strings(manifest, "platforms", required=True)
    requires = _strings(manifest, "requires", required=True)
    if not platforms or not requires:
        raise CatalogError("manifest platforms and requires cannot be empty")
    result = {
        key: manifest.get(key, "")
        for key in (
            "id",
            "name",
            "version",
            "min_swe_mux_version",
            "description",
            "author",
            "license",
            "homepage",
        )
    }
    result.update(
        {
            "platforms": platforms,
            "architectures": _strings(manifest, "architectures"),
            "requires": requires,
            "permissions": _strings(manifest, "permissions"),
            "runtime_requirements": _strings(manifest, "runtime_requirements"),
            "contributions": {
                name: len(manifest.get(name, [])) if isinstance(manifest.get(name, []), list) else 0
                for name in ("actions", "panes", "events", "startup", "link_handlers")
            },
        }
    )
    return result


def _manifest_at(client: GitHub, full_name: str, ref: str) -> dict[str, Any]:
    encoded_ref = urllib.parse.quote(ref, safe="")
    payload = client.get(
        f"/repos/{full_name}/contents/swe-mux-plugin.toml?ref={encoded_ref}"
    )
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise CatalogError("root swe-mux-plugin.toml is missing")
    try:
        encoded = "".join(str(payload.get("content") or "").split())
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise CatalogError("manifest content is not valid base64") from exc
    return validate_manifest(raw)


def _commit(client: GitHub, full_name: str, ref: str) -> str:
    encoded_ref = urllib.parse.quote(ref, safe="")
    payload = client.get(f"/repos/{full_name}/commits/{encoded_ref}")
    sha = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise CatalogError(f"GitHub returned no commit for {ref}")
    return sha


def build_listing(client: GitHub, repository: dict[str, Any]) -> dict[str, Any]:
    full_name = str(repository.get("full_name") or "")
    if not full_name or repository.get("fork") or repository.get("archived"):
        raise CatalogError("repository is forked, archived, or unnamed")
    default_branch = str(repository.get("default_branch") or "")
    if not default_branch:
        raise CatalogError("repository has no default branch")
    default_sha = _commit(client, full_name, default_branch)
    default_manifest = _manifest_at(client, full_name, default_sha)
    release = client.get(f"/repos/{full_name}/releases/latest", allow_missing=True)
    indexed_ref = default_sha
    manifest = default_manifest
    install_ref = ""
    release_url = ""
    release_published_at = ""
    if isinstance(release, dict):
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag:
            raise CatalogError("latest release has no tag")
        indexed_ref = _commit(client, full_name, tag)
        manifest = _manifest_at(client, full_name, indexed_ref)
        if tag.removeprefix("v") != manifest["version"]:
            raise CatalogError("latest release tag does not match manifest version")
        install_ref = tag
        release_url = str(release.get("html_url") or "")
        release_published_at = str(release.get("published_at") or "")
    official = full_name in OFFICIAL
    if official:
        if manifest["id"] != OFFICIAL[full_name]:
            raise CatalogError("official repository has the wrong permanent plugin id")
        if not install_ref:
            raise CatalogError("official repository has no GitHub release")
    license_value = repository.get("license")
    license_id = license_value.get("spdx_id") if isinstance(license_value, dict) else None
    owner = repository.get("owner")
    return {
        "official": official,
        "unreviewed": not official,
        "indexed_ref": indexed_ref,
        "default_ref": default_sha,
        "install_ref": install_ref,
        "release_url": release_url,
        "release_published_at": release_published_at,
        "repository": {
            "name": repository.get("name"),
            "full_name": full_name,
            "owner": owner.get("login") if isinstance(owner, dict) else "",
            "description": repository.get("description") or "",
            "stars": int(repository.get("stargazers_count") or 0),
            "language": repository.get("language"),
            "updated_at": repository.get("updated_at"),
            "url": repository.get("html_url"),
            "license": license_id,
        },
        "manifest": manifest,
    }


def build_catalog(client: GitHub) -> dict[str, Any]:
    query = urllib.parse.urlencode({"q": TOPIC_QUERY, "sort": "updated", "per_page": 100})
    search = client.get(f"/search/repositories?{query}")
    items = search.get("items") if isinstance(search, dict) else None
    if not isinstance(items, list):
        raise CatalogError("GitHub search returned no repository list")
    repositories = {
        str(item.get("full_name")): item for item in items if isinstance(item, dict)
    }
    for full_name in OFFICIAL:
        if full_name not in repositories:
            repositories[full_name] = client.get(f"/repos/{full_name}")
    log.info("discovered repositories=%s", len(repositories))
    plugins: list[dict[str, Any]] = []
    failures: list[str] = []
    ids: set[str] = set()
    for full_name, repository in sorted(repositories.items()):
        try:
            listing = build_listing(client, repository)
            plugin_id = listing["manifest"]["id"]
            if plugin_id in ids:
                raise CatalogError(f"duplicate plugin id {plugin_id}")
            ids.add(plugin_id)
            plugins.append(listing)
            log.info(
                "indexed repository=%s plugin_id=%s revision=%s",
                full_name,
                plugin_id,
                listing["indexed_ref"],
            )
        except CatalogError as exc:
            failures.append(f"{full_name}: {exc}")
            log.warning("excluded repository=%s reason=%s", full_name, exc)
    missing_official = sorted(set(OFFICIAL) - {item["repository"]["full_name"] for item in plugins})
    if missing_official:
        raise CatalogError("official plugins failed validation: " + ", ".join(missing_official))
    plugins.sort(
        key=lambda item: (
            not item["official"],
            str(item["manifest"]["name"]).casefold(),
            str(item["manifest"]["id"]),
        )
    )
    return {
        "schema": 1,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": "github-topic",
        "topic": "swe-mux-plugin",
        "plugins": plugins,
        "excluded": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("site/plugins/catalog.json"))
    parser.add_argument("--script-output", type=Path, default=Path("site/plugins/catalog.js"))
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s component=plugin-catalog %(message)s",
    )
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    client = GitHub(token)
    catalog = build_catalog(client)
    rendered = json.dumps(catalog, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    args.script_output.parent.mkdir(parents=True, exist_ok=True)
    args.script_output.write_text(
        "window.SWEMUX_PLUGIN_CATALOG = " + rendered + ";\n",
        encoding="utf-8",
        newline="\n",
    )
    log.info(
        "catalog written path=%s script_path=%s plugins=%s excluded=%s requests=%s",
        args.output,
        args.script_output,
        len(catalog["plugins"]),
        len(catalog["excluded"]),
        client.requests,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
