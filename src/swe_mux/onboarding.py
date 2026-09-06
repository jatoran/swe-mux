"""Durable, revision-checked setup and tour progress shared by every client.

This file contains no credentials or browser geometry. Deferral is distinct from
completion; a new browser never invents a new installation. Writes are atomic,
and an unreadable record is preserved before a replacement is created.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from .config import Config

log = logging.getLogger(__name__)
STEPS = (
    "existing",
    "experience",
    "provider",
    "harnesses",
    "projects",
    "keymap",
    "permissions",
    "extras",
    "voice",
    "phone",
    "desktop",
    "finish",
    "complete",
)
STATUSES = ("active", "deferred", "complete")
QUESTS = ("project", "session", "phone", "voice", "desktop", "worktrees", "provider", "permissions")
TOUR_STATUSES = ("pending", "active", "deferred", "complete")


def installation_identity() -> str:
    from .install_location import detect_install_location

    location = detect_install_location()
    return os.path.normcase(str(location.environment_root.resolve()))


def initial_state(config: Config, installation: str) -> dict[str, Any]:
    existing = config.harness_setup_complete
    return {
        "version": 1,
        "revision": 0,
        "installation": installation,
        "step": "existing" if existing else "experience",
        "status": "active",
        "hidden": False,
        "tour_status": "deferred" if existing else "pending",
        "tour_step": "welcome",
        "dismissed": list(config.quests_dismissed),
        "completed": [],
        "draft": {},
        "updated_at": time.time(),
    }


def write_state(config: Config, state: dict[str, Any]) -> None:
    directory = config.data_dir
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "onboarding.json"
    staged = directory / "onboarding.json.tmp"
    with staged.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    staged.replace(target)


def read_state(config: Config, *, installation: str | None = None) -> dict[str, Any]:
    identity = installation if installation is not None else installation_identity()
    path = config.data_dir / "onboarding.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("unsupported onboarding record")
        validate_patch({key: state[key] for key in EDITABLE})
        if type(state["revision"]) is not int or state["revision"] < 0:
            raise ValueError("invalid revision")
        if not isinstance(state["installation"], str):
            raise ValueError("invalid installation identity")
    except FileNotFoundError:
        state = initial_state(config, identity)
        write_state(config, state)
        log.info("onboarding initialized", extra={"step": state["step"]})
    except (ValueError, KeyError, TypeError):
        backup = path.with_name(f"onboarding.invalid-{uuid4().hex}.json")
        shutil.copy2(path, backup)
        log.exception("onboarding record unreadable; preserved", extra={"backup": str(backup)})
        state = initial_state(config, identity)
        state.update(step="existing", status="active")
        write_state(config, state)
    if state["installation"] != identity:
        state.update(installation=identity, step="existing", status="active", hidden=False)
        state["revision"] += 1
        write_state(config, state)
        log.info("onboarding installation changed; offering retained settings")
    return cast(dict[str, Any], state)


EDITABLE = frozenset(
    {"step", "status", "hidden", "tour_status", "tour_step", "dismissed", "completed", "draft"}
)
DRAFT_KEYS = frozenset(
    {
        "tier",
        "autonomy",
        "autonomy_overrides",
        "provider",
        "overrides",
        "theme",
        "keymap",
        "keymap_applied",
        "fleet_access",
        "harnesses",
        "default_harness",
        "scan_history",
        "rail_desktop",
        "rail_mobile",
        "core_complete",
        "applied_experience",
        "model_features_pending",
        "project_id",
        "project_path",
        "project_name",
        "project_filter",
        "selected_projects",
        "provider_return",
        "voice",
    }
)


def validate_patch(patch: dict[str, Any]) -> None:
    if set(patch) - EDITABLE:
        raise ValueError("unknown onboarding field")
    for key, choices in (("step", STEPS), ("status", STATUSES), ("tour_status", TOUR_STATUSES)):
        if key in patch and patch[key] not in choices:
            raise ValueError(f"invalid {key}")
    if "hidden" in patch and not isinstance(patch["hidden"], bool):
        raise ValueError("hidden must be a boolean")
    if "tour_step" in patch and (
        not isinstance(patch["tour_step"], str) or len(patch["tour_step"]) > 64
    ):
        raise ValueError("invalid tour step")
    for key in ("dismissed", "completed"):
        if key in patch:
            value = patch[key]
            if (
                not isinstance(value, list)
                or any(item not in QUESTS for item in value)
                or len(value) != len(set(value))
            ):
                raise ValueError(f"invalid {key}")
    if "draft" in patch:
        draft = patch["draft"]
        if not isinstance(draft, dict) or set(draft) - DRAFT_KEYS:
            raise ValueError("invalid setup draft; credentials cannot be stored here")
        for key, choices in (
            ("tier", ("terminal", "deterministic", "automations")),
            ("applied_experience", ("terminal", "deterministic", "automations")),
            ("autonomy", ("supervised", "assisted", "autonomous")),
            ("fleet_access", ("default", "mcp", "cli", "none")),
        ):
            if key in draft and draft[key] not in choices:
                raise ValueError(f"invalid draft {key}")
        for key in ("theme", "keymap", "keymap_applied", "default_harness"):
            if key in draft and (not isinstance(draft[key], str) or len(draft[key]) > 100):
                raise ValueError(f"invalid draft {key}")
        for key in (
            "scan_history",
            "rail_desktop",
            "rail_mobile",
            "core_complete",
            "model_features_pending",
        ):
            if key in draft and not isinstance(draft[key], bool):
                raise ValueError(f"invalid draft {key}")
        for key in ("project_id", "project_path", "project_name", "project_filter"):
            if key in draft and (not isinstance(draft[key], str) or len(draft[key]) > 2048):
                raise ValueError(f"invalid draft {key}")
        if "provider_return" in draft and draft["provider_return"] not in (
            "permissions",
            "extras",
            "voice",
        ):
            raise ValueError("invalid provider return step")
        if "selected_projects" in draft:
            selected = draft["selected_projects"]
            if (
                not isinstance(selected, list)
                or len(selected) > 200
                or any(not isinstance(path, str) or len(path) > 2048 for path in selected)
            ):
                raise ValueError("invalid selected projects")
        if "voice" in draft:
            validate_voice_draft(draft["voice"])
        if "provider" in draft:
            validate_provider_draft(draft["provider"])
        if "autonomy_overrides" in draft:
            validate_autonomy_draft(draft["autonomy_overrides"])
        for key in ("harnesses", "overrides"):
            if key in draft and (
                not isinstance(draft[key], dict)
                or any(not isinstance(value, bool) for value in draft[key].values())
            ):
                raise ValueError(f"invalid draft {key}")
        if len(json.dumps(draft)) > 128000:
            raise ValueError("setup draft too large")


def validate_voice_draft(voice: Any) -> None:
    flags = {"read_aloud", "dictation", "summaries", "assistant"}
    choices = {
        "step": ("choices", "install", "test", "provider"),
        "tts_engine": ("sapi", "kokoro"),
        "stt_engine": ("sapi", "whisper"),
    }
    if not isinstance(voice, dict) or set(voice) - flags - choices.keys():
        raise ValueError("invalid voice setup draft")
    for key, value in voice.items():
        if (key in flags and not isinstance(value, bool)) or (
            key in choices and value not in choices[key]
        ):
            raise ValueError(f"invalid voice setup {key}")


def validate_autonomy_draft(overrides: Any) -> None:
    from .experience_tiers import autonomy_changes

    keys = {key for key, value in autonomy_changes("supervised").items() if type(value) is int}
    if (
        not isinstance(overrides, dict)
        or set(overrides) - keys
        or any(type(value) is not int or value < 1 for value in overrides.values())
    ):
        raise ValueError("invalid automatic-delivery limits")


def validate_provider_draft(provider: Any) -> None:
    from .budget import coerce_budget, validate
    from .config import BUDGET_FIELDS

    strings = {
        "llm_provider",
        "custom_llm_base_url",
        "custom_llm_model",
        "custom_llm_catalog_url",
        "openrouter_cheap_model",
        "openrouter_standard_model",
    }
    if not isinstance(provider, dict) or set(provider) - strings - {"automation_daily_budget"}:
        raise ValueError("invalid provider setup draft; credentials cannot be stored here")
    if any(
        not isinstance(value, str) or len(value) > 2048
        for key, value in provider.items()
        if key in strings
    ):
        raise ValueError("invalid provider setup field")
    if "llm_provider" in provider and provider["llm_provider"] not in ("openrouter", "custom"):
        raise ValueError("invalid model provider")
    if "automation_daily_budget" in provider:
        value = provider["automation_daily_budget"]
        if not isinstance(value, dict) or set(value) != {"tokens", "usd", "mode"}:
            raise ValueError("invalid setup spending limit")
        if value["mode"] not in ("tokens", "usd", "either") or any(
            number is not None and (type(number) not in (int, float) or not math.isfinite(number))
            for number in (value["tokens"], value["usd"])
        ):
            raise ValueError("invalid setup spending limit")
        spec = BUDGET_FIELDS["automation_daily_budget"]
        errors: dict[str, str] = {}
        validate(
            coerce_budget(value, fallback=spec.default),
            field=spec.field,
            errors=errors,
            max_tokens=spec.max_tokens,
            max_usd=spec.max_usd,
            min_tokens=spec.min_tokens,
            min_usd=spec.min_usd,
        )
        if errors:
            raise ValueError("; ".join(errors.values()))


def change_state(config: Config, patch: dict[str, Any], revision: int) -> dict[str, Any]:
    validate_patch(patch)
    current = read_state(config)
    if revision != current["revision"]:
        raise ValueError("onboarding changed on another client; reload and try again")
    state = {**copy.deepcopy(current), **patch, "revision": revision + 1, "updated_at": time.time()}
    write_state(config, state)
    log.info(
        "onboarding progress saved",
        extra={
            "step": state["step"],
            "status": state["status"],
            "tour_status": state["tour_status"],
            "revision": state["revision"],
            "fields": ",".join(sorted(patch)),
        },
    )
    return state


def backup_preferences(config: Config) -> Path:
    """A recoverable preferences snapshot; project files and account stores stay put."""
    target = config.data_dir / "setup-backups" / f"{int(time.time())}-{uuid4().hex[:8]}"
    target.mkdir(parents=True)
    if config.config_path and config.config_path.is_file():
        shutil.copy2(config.config_path, target / "config.toml")
    for name in ("onboarding.json", "keybindings.json"):
        source = config.data_dir / name
        if source.is_file():
            shutil.copy2(source, target / name)
    log.info("onboarding preferences backed up", extra={"backup": str(target)})
    return target
