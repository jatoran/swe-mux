"""English message expansion for legacy source-contract tests.

Only catalog expressions change here.
Conditions, handlers and markup come from the actual TypeScript syntax tree.
New localization behavior is tested in the browser, not by inspecting this view.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

_CATALOG = Path(__file__).parents[2] / "frontend/src/localization/catalogs/en.json"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    return json.loads(_CATALOG.read_text(encoding="utf-8"))


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


@lru_cache(maxsize=64)
def localized_source(source: str) -> str:
    if 't("' not in source and "t('" not in source and 'rich("' not in source:
        return source
    raw = source.encode("utf-8")
    tree = get_parser("tsx").parse(raw)

    def text(node: Any) -> str:
        return raw[node.start_byte : node.end_byte].decode("utf-8")

    def pattern(value: str, parameters: dict[str, Any], jsx: bool) -> str:
        at = 0

        def read(stop: bool = False) -> str:
            nonlocal at
            out = ""
            while at < len(value):
                char = value[at]
                if char == "}" and stop:
                    at += 1
                    return out
                if char == "'":
                    if value[at : at + 2] == "''":
                        out += "'"
                        at += 2
                        continue
                    if at + 1 < len(value) and value[at + 1] in "{}#":
                        end = value.find("'", at + 1)
                        if end >= 0:
                            out += value[at + 1 : end]
                            at = end + 1
                            continue
                    out += char
                    at += 1
                    continue
                if char != "{":
                    out += char
                    at += 1
                    continue
                at += 1
                start = at
                while at < len(value) and value[at] not in ",}":
                    at += 1
                name = value[start:at].strip()
                node = parameters.get(name)
                if at < len(value) and value[at] == "}":
                    at += 1
                    expression = render(node) if node is not None else name
                    if jsx and node is not None and node.type.startswith("jsx_"):
                        out += expression
                    else:
                        out += ("" if jsx else "$") + "{" + expression + "}"
                    continue
                at += 1
                start = at
                while at < len(value) and value[at] not in ",}":
                    at += 1
                kind = value[start:at].strip()
                at += 1
                choices: dict[str, str] = {}
                if kind in {"select", "plural", "selectordinal"}:
                    while at < len(value):
                        while at < len(value) and value[at].isspace():
                            at += 1
                        if value[at : at + 1] == "}":
                            at += 1
                            break
                        start = at
                        while at < len(value) and value[at] != "{":
                            at += 1
                        choice = value[start:at].strip()
                        at += 1
                        choices[choice] = read(True)
                    condition = node.child_by_field_name("condition") if node is not None else None
                    if condition is not None and "yes" in choices:
                        if condition.type == "parenthesized_expression":
                            condition = condition.named_children[0]
                        expression = (
                            render(condition)
                            + " ? "
                            + _quote(choices["yes"])
                            + " : "
                            + _quote(choices.get("other", ""))
                        )
                        out += ("" if jsx else "$") + "{" + expression + "}"
                    else:
                        out += choices.get("other", "")
                else:
                    while at < len(value) and value[at] != "}":
                        at += 1
                    at += 1
                    expression = render(node) if node is not None else name
                    out += ("" if jsx else "$") + "{" + expression + "}"
            return out

        return read()

    def message(node: Any, jsx: bool) -> str | None:
        if node.type != "call_expression":
            return None
        function = node.child_by_field_name("function")
        if function is None or text(function) not in {"t", "rich"}:
            return None
        args_node = node.child_by_field_name("arguments")
        args = args_node.named_children if args_node is not None else []
        if not args or args[0].type != "string":
            return None
        key = text(args[0])[1:-1]
        template = _catalog().get(key)
        if template is None:
            return None
        parameters = {}
        if len(args) > 1 and args[1].type == "object":
            for pair in args[1].named_children:
                if pair.type != "pair":
                    continue
                name = pair.child_by_field_name("key")
                val = pair.child_by_field_name("value")
                if name is not None and val is not None:
                    parameters[text(name).strip("'\"")] = val
        result = pattern(template, parameters, jsx)
        if jsx:
            return result
        return chr(96) + result + chr(96) if parameters else _quote(result)

    def render(node: Any) -> str:
        if node.type == "jsx_expression" and node.named_children:
            call = node.named_children[0]
            attribute = node.parent is not None and node.parent.type == "jsx_attribute"
            expanded = message(call, not attribute)
            if expanded is not None:
                if not attribute:
                    return expanded
                if expanded.startswith(chr(96)):
                    return "{" + expanded + "}"
                return '"' + expanded[1:-1].replace('"', "&quot;") + '"'
        if node.type == "method_definition" and text(node).startswith("get "):
            body = node.child_by_field_name("body")
            name = node.child_by_field_name("name")
            if body is not None and name is not None and len(body.named_children) == 1:
                returned = body.named_children[0]
                if returned.type == "return_statement" and returned.named_children:
                    expanded = message(returned.named_children[0], False)
                    if expanded is not None:
                        return text(name) + ":" + expanded
        expanded = message(node, False)
        if expanded is not None:
            return expanded
        out = ""
        at = node.start_byte
        for child in node.named_children:
            out += raw[at : child.start_byte].decode("utf-8") + render(child)
            at = child.end_byte
        return out + raw[at : node.end_byte].decode("utf-8")

    return render(tree.root_node)


def source_contains(source: str, fragment: str) -> bool:
    """Compare a structural fragment independently of source layout and locale annotations."""
    import re

    def normalize(value: str) -> str:
        value = re.sub(r'\s+data-heading-message="[^"]+"', "", value)
        return re.sub(r"\s+", "", value)

    return normalize(fragment) in normalize(source)
