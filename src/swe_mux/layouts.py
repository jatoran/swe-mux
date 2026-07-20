from __future__ import annotations

from typing import Any
from uuid import uuid4

LAYOUT_VERSION = 6
MAX_LAYOUT_LEAVES = 64
MAX_LAYOUT_DEPTH = 24
LEAF_KINDS = {"terminal", "note", "preview"}
SPLIT_DIRECTIONS = {"horizontal", "vertical"}


def _leaf(kind: str, resource_id: str) -> dict[str, Any]:
    return {"type": "leaf", "kind": kind, "id": resource_id}


def _group_id(value: object = None) -> str:
    return str(value) if isinstance(value, str) and value else f"group-{uuid4().hex[:12]}"


def _pane(
    children: list[dict[str, Any]], active_id: object = None, pane_id: object = None
) -> dict[str, Any]:
    ids = [str(child["id"]) for child in children]
    return {
        "type": "stack",
        "id": _group_id(pane_id),
        "children": children,
        "active_child_id": str(active_id) if active_id in ids else ids[0],
    }


def _ratio(value: object, *, default: float = 0.5) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("layout split ratio must be numeric")
    normalized = round(float(value), 4)
    if not 0.1 <= normalized <= 0.9:
        raise ValueError("layout split ratio must be between 0.1 and 0.9")
    return normalized


def _balanced_terminals(ids: list[str]) -> dict[str, Any] | None:
    if not ids:
        return None
    if len(ids) == 1:
        return _pane([_leaf("terminal", ids[0])])
    midpoint = (len(ids) + 1) // 2
    return {
        "type": "split",
        "id": _group_id(),
        "direction": "horizontal" if len(ids) in {2, 4} else "vertical",
        "ratio": 0.5,
        "first": _balanced_terminals(ids[:midpoint]),
        "second": _balanced_terminals(ids[midpoint:]),
    }


def _validated_leaf(value: object, seen: set[str], leaves: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("layout pane children must be leaves")
    kind, resource_id = value.get("kind"), value.get("id")
    if (
        value.get("type") != "leaf"
        or kind not in LEAF_KINDS
        or not isinstance(resource_id, str)
        or not resource_id
    ):
        raise ValueError("layout leaf requires a supported kind and non-empty id")
    if resource_id in seen:
        raise ValueError("layout cannot contain the same resource more than once")
    seen.add(resource_id)
    leaf = _leaf(str(kind), resource_id)
    leaves.append(leaf)
    if len(leaves) > MAX_LAYOUT_LEAVES:
        raise ValueError(f"layout exceeds maximum leaf count {MAX_LAYOUT_LEAVES}")
    return leaf


def _validate_node(
    node: object,
    *,
    depth: int,
    seen: set[str],
    leaves: list[dict[str, Any]],
    legacy: bool = False,
) -> dict[str, Any]:
    if depth > MAX_LAYOUT_DEPTH:
        raise ValueError(f"layout exceeds maximum depth {MAX_LAYOUT_DEPTH}")
    if not isinstance(node, dict):
        raise ValueError("layout node must be an object")
    node_type = node.get("type")
    if legacy and node_type == "leaf":
        return _pane([_validated_leaf(node, seen, leaves)])
    if node_type == "stack":
        children = node.get("children")
        if not isinstance(children, list) or not children:
            raise ValueError("layout pane requires at least one tab")
        normalized = [_validated_leaf(child, seen, leaves) for child in children]
        return _pane(normalized, node.get("active_child_id"), node.get("id"))
    if node_type == "split":
        direction = node.get("direction")
        if direction not in SPLIT_DIRECTIONS:
            raise ValueError("layout split direction must be horizontal or vertical")
        return {
            "type": "split",
            "id": _group_id(node.get("id")),
            "direction": direction,
            "ratio": _ratio(node.get("ratio", 0.5)),
            "first": _validate_node(
                node.get("first"), depth=depth + 1, seen=seen, leaves=leaves, legacy=legacy
            ),
            "second": _validate_node(
                node.get("second"), depth=depth + 1, seen=seen, leaves=leaves, legacy=legacy
            ),
        }
    raise ValueError("layout node type must be stack or split")


def _migrate_resource_workspace(
    workspace: object, seen: set[str], leaves: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, float]:
    if not isinstance(workspace, dict) or not workspace.get("visible"):
        return None, 0.38
    raw_ids = workspace.get("open_ids", [])
    if not isinstance(raw_ids, list):
        raise ValueError("layout note workspace open_ids must be an array")
    children: list[dict[str, Any]] = []
    for resource_id in raw_ids:
        if not isinstance(resource_id, str) or not resource_id:
            raise ValueError("layout note workspace ids must be non-empty strings")
        if resource_id in seen:
            continue
        children.append(_validated_leaf(_leaf("note", resource_id), seen, leaves))
    raw_size = workspace.get("size", 0.38)
    if not isinstance(raw_size, (int, float)) or isinstance(raw_size, bool):
        raise ValueError("layout note workspace size must be numeric")
    size = max(0.2, min(0.7, round(float(raw_size), 4)))
    return (_pane(children, workspace.get("active_id")) if children else None), size


def normalize_layout(layout: object) -> dict[str, Any]:
    """Validate layout v6 or migrate v1-v5 into one mixed-view pane tree."""
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
    if version not in {2, 3, 4, 5, LAYOUT_VERSION}:
        raise ValueError(f"unsupported layout version {version}")
    root = layout.get("root")
    seen: set[str] = set()
    leaves: list[dict[str, Any]] = []
    normalized = (
        None
        if root is None
        else _validate_node(
            root, depth=0, seen=seen, leaves=leaves, legacy=version != LAYOUT_VERSION
        )
    )
    if version in {4, 5}:
        raw_workspace = layout.get("note_workspace") if version == 5 else layout.get("note_dock")
        if version == 4 and isinstance(raw_workspace, dict):
            raw_workspace = {**raw_workspace, "visible": True}
        resource_pane, size = _migrate_resource_workspace(raw_workspace, seen, leaves)
        if resource_pane is not None:
            normalized = (
                resource_pane
                if normalized is None
                else {
                    "type": "split",
                    "id": _group_id(),
                    "direction": "horizontal",
                    "ratio": round(1.0 - size, 4),
                    "first": normalized,
                    "second": resource_pane,
                }
            )
    return {"version": LAYOUT_VERSION, "root": normalized}


def _layout_leaves(layout: object) -> list[dict[str, Any]]:
    normalized = normalize_layout(layout)
    result: list[dict[str, Any]] = []

    def visit(node: dict[str, Any] | None) -> None:
        if node is None:
            return
        if node["type"] == "stack":
            result.extend(node["children"])
            return
        visit(node["first"])
        visit(node["second"])

    visit(normalized["root"])
    return result


def layout_terminal_ids(layout: object) -> list[str]:
    return [str(leaf["id"]) for leaf in _layout_leaves(layout) if leaf["kind"] == "terminal"]


def _pane_containing(root: dict[str, Any] | None, resource_id: str | None) -> dict[str, Any] | None:
    panes: list[dict[str, Any]] = []

    def visit(node: dict[str, Any] | None) -> None:
        if node is None:
            return
        if node["type"] == "stack":
            panes.append(node)
            return
        visit(node["first"])
        visit(node["second"])

    visit(root)
    if resource_id:
        found = next(
            (pane for pane in panes if resource_id in [child["id"] for child in pane["children"]]),
            None,
        )
        if found is not None:
            return found
    return panes[0] if panes else None


def _replace_pane(node: dict[str, Any], pane_id: str, replacement: Any) -> dict[str, Any]:
    if node["type"] == "stack":
        return replacement(node) if node["id"] == pane_id else node
    return {
        **node,
        "first": _replace_pane(node["first"], pane_id, replacement),
        "second": _replace_pane(node["second"], pane_id, replacement),
    }


def attach_terminal(
    layout: object, session_id: str, *, target_id: str | None = None, direction: str | None = None
) -> dict[str, Any]:
    return attach_leaf(layout, "terminal", session_id, target_id=target_id, direction=direction)


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
    if any(leaf["id"] == resource_id for leaf in _layout_leaves(normalized)):
        return normalized
    next_leaf = _leaf(kind, resource_id)
    root = normalized["root"]
    if root is None:
        return {**normalized, "root": _pane([next_leaf])}
    target = _pane_containing(root, target_id)
    if target is None:
        return normalized
    if direction in SPLIT_DIRECTIONS:
        next_pane = _pane([next_leaf])
        root = _replace_pane(
            root,
            target["id"],
            lambda pane: {
                "type": "split",
                "id": _group_id(),
                "direction": direction,
                "ratio": 0.5,
                "first": pane,
                "second": next_pane,
            },
        )
    else:
        root = _replace_pane(
            root,
            target["id"],
            lambda pane: {
                **pane,
                "children": [*pane["children"], next_leaf],
                "active_child_id": resource_id,
            },
        )
    return normalize_layout({**normalized, "root": root})


def stack_leaf(
    layout: object, kind: str, resource_id: str, *, target_id: str
) -> dict[str, Any] | None:
    if kind not in LEAF_KINDS:
        raise ValueError(f"layout panes cannot hold {kind} leaves")
    normalized = normalize_layout(layout)
    if any(leaf["id"] == resource_id for leaf in _layout_leaves(normalized)):
        return normalized
    target = _pane_containing(normalized["root"], target_id)
    if target is None or target_id not in [child["id"] for child in target["children"]]:
        return None
    root = _replace_pane(
        normalized["root"],
        target["id"],
        lambda pane: {
            **pane,
            "children": [*pane["children"], _leaf(kind, resource_id)],
            "active_child_id": resource_id,
        },
    )
    return normalize_layout({**normalized, "root": root})


def remove_layout_leaf(layout: object, kind: str, resource_id: str) -> dict[str, Any]:
    normalized = normalize_layout(layout)

    def remove(node: dict[str, Any] | None) -> dict[str, Any] | None:
        if node is None:
            return None
        if node["type"] == "stack":
            children = [
                child
                for child in node["children"]
                if not (child["kind"] == kind and child["id"] == resource_id)
            ]
            if not children:
                return None
            active = node["active_child_id"]
            return {
                **node,
                "children": children,
                "active_child_id": active
                if active in {child["id"] for child in children}
                else children[0]["id"],
            }
        first, second = remove(node["first"]), remove(node["second"])
        if first is None:
            return second
        if second is None:
            return first
        return {**node, "first": first, "second": second}

    return normalize_layout({**normalized, "root": remove(normalized["root"])})
