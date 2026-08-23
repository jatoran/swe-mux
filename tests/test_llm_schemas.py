"""Every schema sent as a `strict` json_schema must satisfy strict mode's rules.

`OpenRouterClient.complete_json` sends every caller's schema with `strict: True`,
which is not a stylistic setting: it is what makes `require_parameters` routing
return the schema instead of prose. Strict mode imposes two rules that ordinary
JSON Schema does not, and a schema that breaks either is rejected by the provider
with HTTP 400 *before a single token is billed*:

1. `required` must list every key in `properties`. An "optional" property is not
   a looser contract, it is a call that can never succeed.
2. `additionalProperties` must be `false` on every object.

The failure this guards is silent and expensive to find. `TITLE_SCHEMA` shipped
with `confidence` declared but not required, so `builtin:adaptive-title` failed
100% of its calls from the day it was enabled. Nothing surfaced it: a rejected
call bills nothing, so it wrote no spend row, and the feature reads as simply
never having fired. It was found by replaying the call against the live provider
(reported 2026-08-23).

The in-tree consumer tests could not catch it, and a similar test never will: they
inject a fake provider that ignores the `schema` argument entirely and returns a
canned value, which is the right thing for testing pivot logic and exactly why the
schema itself needs its own guard.

Discovery scans the source for `*_SCHEMA` / `*_SCHEMAS` constants rather than
importing a curated list, mirroring `test_spend_label_matrix.py`: the failure mode
is a schema nobody remembered to add. Resolution is by import rather than by
parsing the literal, because `SCAN_SCHEMA` builds its enums with `sorted(...)` and
a purely syntactic reader would skip the largest schema in the package while
appearing to cover it.

Scope is `complete_json` only. `complete_tools` sends no `strict` flag and no
response format, so the assistant's tool-parameter schemas may legitimately leave
a parameter optional; they are not named `*_SCHEMA` and are not collected here.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parents[1] / "src" / "swe_mux"
SCHEMA_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*_SCHEMAS?)\b\s*(?::[^=\n]+)?=", re.M)

# The schemas known to exist when this guard was written. The floor is asserted so
# a drifted pattern fails loudly instead of passing over an empty scan.
KNOWN_SCHEMAS = {
    "attention_narration.NARRATION_SCHEMA",
    "automation.OBSERVER_SCHEMAS",
    "behavioral_consumers.TITLE_SCHEMA",
    "project_card.PROJECT_CARD_SCHEMA",
    "scan_timeline.SCAN_SCHEMA",
    "voice.SUMMARY_SCHEMA",
}


def _looks_like_schema(value: Any) -> bool:
    return isinstance(value, dict) and ("properties" in value or value.get("type") == "object")


def discovered_schemas() -> dict[str, dict[str, Any]]:
    """Every module-level JSON schema in the package, by `module.CONSTANT` name.

    A `*_SCHEMAS` mapping of name to schema is expanded to its entries, and the
    SQL DDL constants that share the naming convention (`AUTOMATION_SCHEMA` and
    friends, all `str`) fall out because they are not dicts.
    """
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(SOURCE.glob("*.py")):
        names = SCHEMA_ASSIGNMENT.findall(path.read_text(encoding="utf-8"))
        if not names:
            continue
        module = importlib.import_module(f"swe_mux.{path.stem}")
        for name in names:
            value = getattr(module, name, None)
            label = f"{path.stem}.{name}"
            if _looks_like_schema(value):
                found[label] = value
            elif isinstance(value, dict):
                for key, item in value.items():
                    if _looks_like_schema(item):
                        found[f"{label}[{key}]"] = item
    return found


def strict_violations(schema: Any, path: str) -> list[str]:
    """Every strict-mode rule this schema (or a schema nested in it) breaks."""
    problems: list[str] = []
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            required = schema.get("required")
            missing = sorted(set(properties) - set(required or []))
            if missing:
                problems.append(
                    f"{path}: `required` omits {missing}; strict mode needs every key in "
                    "`properties`, so the provider rejects this schema with HTTP 400"
                )
            if schema.get("additionalProperties") is not False:
                problems.append(
                    f"{path}: `additionalProperties` must be false on every object under "
                    "strict mode"
                )
        for key, value in schema.items():
            problems.extend(strict_violations(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            problems.extend(strict_violations(value, f"{path}[{index}]"))
    return problems


def test_discovery_finds_every_known_schema() -> None:
    found = discovered_schemas()
    # `OBSERVER_SCHEMAS` is expanded into its entries, so match on the prefix.
    labels = {label.split("[", 1)[0] for label in found}
    missing = sorted(KNOWN_SCHEMAS - labels)
    assert not missing, (
        "the schema scan no longer finds these, so the guard below would pass "
        f"vacuously over them: {missing}"
    )


def test_every_schema_is_legal_under_strict_mode() -> None:
    found = discovered_schemas()
    assert found, "the schema scan found nothing; the naming convention has drifted"
    problems = [
        problem
        for label, schema in sorted(found.items())
        for problem in strict_violations(schema, label)
    ]
    assert not problems, "\n".join(problems)
