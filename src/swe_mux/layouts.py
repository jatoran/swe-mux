from __future__ import annotations

from typing import Any

LAYOUT_VERSION = 2
MAX_LAYOUT_LEAVES = 64
MAX_LAYOUT_DEPTH = 24
LEAF_KINDS = {"terminal", "note", "preview"}
SPLIT_DIRECTIONS = {"horizontal", "vertical"}


def _leaf(kind: str, resource_id: str) -> dict[str, Any]:
    return {"type": "leaf", "kind": kind, "id": resource_id}


def _balanced_terminals(ids: list[str]) -> dict[str, Any] | None:
    if not ids:
        return None
    if len(ids) == 1:
        return _leaf("terminal", ids[0])
    midpoint = (len(ids) + 1) // 2
    direction = "horizontal" if len(ids) in {2, 4} else "vertical"
    return {
        "type": "split",
        "direction": direction,
        "ratio": 0.5,
        "first": _balanced_terminals(ids[:midpoint]),
        "second": _balanced_terminals(ids[midpoint:]),
    }


def _validate_node(
    node: object,
    *,
    depth: int,
    seen: set[tuple[str, str]],
    leaves: list[dict[str, Any]],
) -> dict[str, Any]:
    if depth > MAX_LAYOUT_DEPTH:
        raise ValueError(f"layout exceeds maximum depth {MAX_LAYOUT_DEPTH}")
    if not isinstance(node, dict):
        raise ValueError("layout node must be an object")
    node_type = node.get("type")
    if node_type == "leaf":
        kind = node.get("kind")
        resource_id = node.get("id")
        if kind not in LEAF_KINDS or not isinstance(resource_id, str) or not resource_id:
            raise ValueError("layout leaf requires a supported kind and non-empty id")
        identity = (kind, resource_id)
        if identity in seen:
            raise ValueError("layout cannot contain the same resource more than once")
        seen.add(identity)
        leaf = _leaf(kind, resource_id)
        leaves.append(leaf)
        if len(leaves) > MAX_LAYOUT_LEAVES:
            raise ValueError(f"layout exceeds maximum leaf count {MAX_LAYOUT_LEAVES}")
        return leaf
    if node_type == "split":
        direction = node.get("direction")
        if direction not in SPLIT_DIRECTIONS:
            raise ValueError("layout split direction must be horizontal or vertical")
        ratio = node.get("ratio", 0.5)
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            raise ValueError("layout split ratio must be numeric")
        normalized_ratio = round(float(ratio), 4)
        if not 0.1 <= normalized_ratio <= 0.9:
            raise ValueError("layout split ratio must be between 0.1 and 0.9")
        return {
            "type": "split",
            "direction": direction,
            "ratio": normalized_ratio,
            "first": _validate_node(
                node.get("first"), depth=depth + 1, seen=seen, leaves=leaves
            ),
            "second": _validate_node(
                node.get("second"), depth=depth + 1, seen=seen, leaves=leaves
            ),
        }
    raise ValueError("layout node type must be leaf or split")


def normalize_layout(layout: object) -> dict[str, Any]:
    """Validate a v2 tree or migrate a legacy v1 pane array into one."""
    if layout is None:
        return {"version": LAYOUT_VERSION, "root": None}
    if not isinstance(layout, dict):
        raise ValueError("layout must be an object")
    version = layout.get("version", 1)
    if version == 1:
        panes = layout.get("panes")
        if not isinstance(panes, list) or not all(isinstance(item, str) for item in panes):
            raise ValueError("layout version 1 requires a string panes array")
        unique = list(dict.fromkeys(item for item in panes if item))
        if len(unique) > MAX_LAYOUT_LEAVES:
            raise ValueError(f"layout exceeds maximum leaf count {MAX_LAYOUT_LEAVES}")
        return {"version": LAYOUT_VERSION, "root": _balanced_terminals(unique)}
    if version != LAYOUT_VERSION:
        raise ValueError(f"unsupported layout version {version}")
    root = layout.get("root")
    if root is None:
        return {"version": LAYOUT_VERSION, "root": None}
    leaves: list[dict[str, Any]] = []
    normalized = _validate_node(root, depth=0, seen=set(), leaves=leaves)
    return {"version": LAYOUT_VERSION, "root": normalized}


def layout_terminal_ids(layout: object) -> list[str]:
    normalized = normalize_layout(layout)
    ids: list[str] = []

    def visit(node: dict[str, Any] | None) -> None:
        if node is None:
            return
        if node["type"] == "leaf":
            if node["kind"] == "terminal":
                ids.append(str(node["id"]))
            return
        visit(node["first"])
        visit(node["second"])

    visit(normalized["root"])
    return ids


def attach_terminal(
    layout: object,
    session_id: str,
    *,
    target_id: str | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    return attach_leaf(
        layout,
        "terminal",
        session_id,
        target_id=target_id,
        direction=direction,
    )


def attach_leaf(
    layout: object,
    kind: str,
    resource_id: str,
    *,
    target_id: str | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    if kind not in LEAF_KINDS:
        raise ValueError(f"unsupported layout leaf kind: {kind}")
    normalized = normalize_layout(layout)
    root = normalized["root"]
    if root is None:
        return {"version": LAYOUT_VERSION, "root": _leaf(kind, resource_id)}
    terminals = layout_terminal_ids(normalized)
    if any(
        leaf["kind"] == kind and leaf["id"] == resource_id
        for leaf in _layout_leaves(normalized)
    ):
        return normalized
    target = target_id if target_id in terminals else (terminals[0] if terminals else None)
    if target is None:
        return normalized

    def replace(node: dict[str, Any]) -> dict[str, Any]:
        if node["type"] == "leaf":
            if node["kind"] != "terminal" or node["id"] != target:
                return node
            next_leaf = _leaf(kind, resource_id)
            if direction in SPLIT_DIRECTIONS:
                return {
                    "type": "split",
                    "direction": direction,
                    "ratio": 0.5,
                    "first": node,
                    "second": next_leaf,
                }
            return next_leaf
        return {**node, "first": replace(node["first"]), "second": replace(node["second"])}

    return normalize_layout({"version": LAYOUT_VERSION, "root": replace(root)})


def _layout_leaves(layout: object) -> list[dict[str, Any]]:
    normalized = normalize_layout(layout)
    leaves: list[dict[str, Any]] = []

    def visit(node: dict[str, Any] | None) -> None:
        if node is None:
            return
        if node["type"] == "leaf":
            leaves.append(node)
            return
        visit(node["first"])
        visit(node["second"])

    visit(normalized["root"])
    return leaves
