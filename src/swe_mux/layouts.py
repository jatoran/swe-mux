from __future__ import annotations

from typing import Any
from uuid import uuid4

LAYOUT_VERSION = 5
MAX_LAYOUT_LEAVES = 64
MAX_LAYOUT_DEPTH = 24
MAX_DOCK_NOTES = 32
LEAF_KINDS = {"terminal", "note", "preview"}
# Tab regions hold processes and their spawned previews side by side. Notes are
# excluded: they live in the space-owned note workspace, not the terminal tree.
STACK_LEAF_KINDS = {"terminal", "preview"}
SPLIT_DIRECTIONS = {"horizontal", "vertical"}


def _leaf(kind: str, resource_id: str) -> dict[str, Any]:
    return {"type": "leaf", "kind": kind, "id": resource_id}


def _group_id(value: object = None) -> str:
    return str(value) if isinstance(value, str) and value else f"group-{uuid4().hex[:12]}"


def _balanced_terminals(ids: list[str]) -> dict[str, Any] | None:
    if not ids:
        return None
    if len(ids) == 1:
        return _leaf("terminal", ids[0])
    midpoint = (len(ids) + 1) // 2
    direction = "horizontal" if len(ids) in {2, 4} else "vertical"
    return {
        "type": "split",
        "id": _group_id(),
        "direction": direction,
        "ratio": 0.5,
        "first": _balanced_terminals(ids[:midpoint]),
        "second": _balanced_terminals(ids[midpoint:]),
    }


def _default_note_workspace() -> dict[str, Any]:
    return {
        "open_ids": [],
        "active_id": None,
        "size": 0.38,
        "visible": False,
        "mode": "dock",
    }


def _normalize_note_workspace(value: object) -> dict[str, Any]:
    if value is None:
        return _default_note_workspace()
    if not isinstance(value, dict):
        raise ValueError("layout note_workspace must be an object")
    raw_ids = value.get("open_ids", [])
    if not isinstance(raw_ids, list) or not all(
        isinstance(item, str) and item for item in raw_ids
    ):
        raise ValueError("layout note_workspace open_ids must contain non-empty strings")
    open_ids = list(dict.fromkeys(raw_ids))
    if len(open_ids) > MAX_DOCK_NOTES:
        raise ValueError(f"layout note dock exceeds maximum note count {MAX_DOCK_NOTES}")
    active = value.get("active_id")
    size = value.get("size", 0.38)
    if not isinstance(size, (int, float)) or isinstance(size, bool):
        raise ValueError("layout note_workspace size must be numeric")
    normalized_size = round(float(size), 4)
    if not 0.2 <= normalized_size <= 0.7:
        raise ValueError("layout note_workspace size must be between 0.2 and 0.7")
    mode = value.get("mode", "dock")
    if mode not in {"dock", "popout"}:
        raise ValueError("layout note_workspace mode must be dock or popout")
    return {
        "open_ids": open_ids,
        "active_id": active if isinstance(active, str) and active in open_ids else (
            open_ids[0] if open_ids else None
        ),
        "size": normalized_size,
        "visible": bool(value.get("visible", bool(open_ids))) and bool(open_ids),
        "mode": mode,
    }


def _extract_note_leaves(node: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    if node is None:
        return None, []
    if node["type"] == "leaf":
        return (None, [str(node["id"])]) if node["kind"] == "note" else (node, [])
    if node["type"] == "stack":
        return node, []
    first, first_notes = _extract_note_leaves(node["first"])
    second, second_notes = _extract_note_leaves(node["second"])
    if first is None:
        return second, [*first_notes, *second_notes]
    if second is None:
        return first, [*first_notes, *second_notes]
    return {**node, "first": first, "second": second}, [*first_notes, *second_notes]


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
            "id": _group_id(node.get("id")),
            "direction": direction,
            "ratio": normalized_ratio,
            "first": _validate_node(node.get("first"), depth=depth + 1, seen=seen, leaves=leaves),
            "second": _validate_node(node.get("second"), depth=depth + 1, seen=seen, leaves=leaves),
        }
    if node_type == "stack":
        children = node.get("children")
        if not isinstance(children, list) or not children:
            raise ValueError("layout stack requires at least one child")
        normalized_children = [
            _validate_node(child, depth=depth + 1, seen=seen, leaves=leaves) for child in children
        ]
        if any(
            child["type"] != "leaf" or child["kind"] not in STACK_LEAF_KINDS
            for child in normalized_children
        ):
            raise ValueError("layout stacks support terminal and preview leaves only")
        active = node.get("active_child_id")
        child_ids = [child["id"] for child in normalized_children]
        return {
            "type": "stack",
            "id": _group_id(node.get("id")),
            "children": normalized_children,
            "active_child_id": active if active in child_ids else child_ids[0],
        }
    raise ValueError("layout node type must be leaf, split, or stack")


def normalize_layout(layout: object) -> dict[str, Any]:
    """Validate a v5 terminal tree/note workspace or migrate older layouts."""
    if layout is None:
        return {
            "version": LAYOUT_VERSION,
            "root": None,
            "note_workspace": _default_note_workspace(),
        }
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
        return {
            "version": LAYOUT_VERSION,
            "root": _balanced_terminals(unique),
            "note_workspace": _default_note_workspace(),
        }
    if version not in {2, 3, 4, LAYOUT_VERSION}:
        raise ValueError(f"unsupported layout version {version}")
    root = layout.get("root")
    normalized: dict[str, Any] | None = None
    if root is not None:
        leaves: list[dict[str, Any]] = []
        normalized = _validate_node(root, depth=0, seen=set(), leaves=leaves)
    normalized, embedded_notes = _extract_note_leaves(normalized)
    raw_workspace = (
        layout.get("note_workspace")
        if version == LAYOUT_VERSION
        else layout.get("note_dock") if version == 4 else None
    )
    workspace = _normalize_note_workspace(raw_workspace)
    workspace["open_ids"] = list(
        dict.fromkeys([*workspace["open_ids"], *embedded_notes])
    )
    if len(workspace["open_ids"]) > MAX_DOCK_NOTES:
        raise ValueError(f"layout note dock exceeds maximum note count {MAX_DOCK_NOTES}")
    if workspace["active_id"] not in workspace["open_ids"]:
        workspace["active_id"] = (
            workspace["open_ids"][0] if workspace["open_ids"] else None
        )
    if embedded_notes:
        workspace["visible"] = True
    if not workspace["open_ids"]:
        workspace["visible"] = False
    return {"version": LAYOUT_VERSION, "root": normalized, "note_workspace": workspace}


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
        if node["type"] == "stack":
            for child in node["children"]:
                visit(child)
        else:
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
    if kind == "note":
        workspace = dict(normalized["note_workspace"])
        if resource_id in workspace["open_ids"]:
            workspace["active_id"] = resource_id
            workspace["visible"] = True
            return {**normalized, "note_workspace": workspace}
        if len(workspace["open_ids"]) >= MAX_DOCK_NOTES:
            raise ValueError(f"layout note dock exceeds maximum note count {MAX_DOCK_NOTES}")
        workspace["open_ids"] = [*workspace["open_ids"], resource_id]
        workspace["active_id"] = resource_id
        workspace["visible"] = True
        return {**normalized, "note_workspace": workspace}
    root = normalized["root"]
    if root is None:
        return {**normalized, "root": _leaf(kind, resource_id)}
    terminals = layout_terminal_ids(normalized)
    if any(
        leaf["kind"] == kind and leaf["id"] == resource_id for leaf in _layout_leaves(normalized)
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
                    "id": _group_id(),
                    "direction": direction,
                    "ratio": 0.5,
                    "first": node,
                    "second": next_leaf,
                }
            return next_leaf
        if node["type"] == "stack":
            if (
                target in [child["id"] for child in node["children"]]
                and direction in SPLIT_DIRECTIONS
            ):
                return {
                    "type": "split",
                    "id": _group_id(),
                    "direction": direction,
                    "ratio": 0.5,
                    "first": node,
                    "second": _leaf(kind, resource_id),
                }
            if target in [child["id"] for child in node["children"]] and kind == "terminal":
                return {
                    **node,
                    "children": [
                        _leaf("terminal", resource_id) if child["id"] == target else child
                        for child in node["children"]
                    ],
                    "active_child_id": resource_id,
                }
            return node
        return {**node, "first": replace(node["first"]), "second": replace(node["second"])}

    return normalize_layout({**normalized, "root": replace(root)})


def stack_leaf(
    layout: object,
    kind: str,
    resource_id: str,
    *,
    target_id: str,
) -> dict[str, Any] | None:
    """Add a leaf as a tab in the region that already holds ``target_id``.

    This keeps a session's spawned previews grouped with the session's own
    terminal instead of splitting them into an unrelated region: a bare target
    leaf becomes a two-tab stack, and an existing stack gains one tab. The new
    tab becomes active.

    Returns ``None`` when the target has no terminal in this layout, so the
    caller can fall back to an ordinary split attach.
    """
    if kind not in STACK_LEAF_KINDS:
        raise ValueError(f"layout stacks cannot hold {kind} leaves")
    normalized = normalize_layout(layout)
    if any(
        leaf["kind"] == kind and leaf["id"] == resource_id for leaf in _layout_leaves(normalized)
    ):
        return normalized
    root = normalized["root"]
    if root is None:
        return {**normalized, "root": _leaf(kind, resource_id)}
    grouped = False

    def visit(node: dict[str, Any]) -> dict[str, Any]:
        nonlocal grouped
        if node["type"] == "leaf":
            if node["kind"] != "terminal" or node["id"] != target_id:
                return node
            grouped = True
            return {
                "type": "stack",
                "id": _group_id(),
                "children": [node, _leaf(kind, resource_id)],
                "active_child_id": resource_id,
            }
        if node["type"] == "stack":
            if not any(child["id"] == target_id for child in node["children"]):
                return node
            grouped = True
            return {
                **node,
                "children": [*node["children"], _leaf(kind, resource_id)],
                "active_child_id": resource_id,
            }
        return {**node, "first": visit(node["first"]), "second": visit(node["second"])}

    updated = visit(root)
    if not grouped:
        return None
    return normalize_layout({**normalized, "root": updated})


def _layout_leaves(layout: object) -> list[dict[str, Any]]:
    normalized = normalize_layout(layout)
    leaves: list[dict[str, Any]] = []

    def visit(node: dict[str, Any] | None) -> None:
        if node is None:
            return
        if node["type"] == "leaf":
            leaves.append(node)
            return
        if node["type"] == "stack":
            for child in node["children"]:
                visit(child)
        else:
            visit(node["first"])
            visit(node["second"])

    visit(normalized["root"])
    leaves.extend(
        _leaf("note", item) for item in normalized["note_workspace"]["open_ids"]
    )
    return leaves


def remove_layout_leaf(layout: object, kind: str, resource_id: str) -> dict[str, Any]:
    """Remove one viewport resource and collapse empty layout containers."""
    normalized = normalize_layout(layout)
    if kind == "note":
        workspace = dict(normalized["note_workspace"])
        workspace["open_ids"] = [
            item for item in workspace["open_ids"] if item != resource_id
        ]
        if workspace["active_id"] == resource_id:
            workspace["active_id"] = (
                workspace["open_ids"][0] if workspace["open_ids"] else None
            )
        if not workspace["open_ids"]:
            workspace["visible"] = False
        return {**normalized, "note_workspace": workspace}

    def remove(node: dict[str, Any] | None) -> dict[str, Any] | None:
        if node is None:
            return None
        if node["type"] == "leaf":
            return None if node["kind"] == kind and node["id"] == resource_id else node
        if node["type"] == "stack":
            children: list[dict[str, Any]] = []
            for child in node["children"]:
                remaining = remove(child)
                if remaining is not None:
                    children.append(remaining)
            if not children:
                return None
            if len(children) == 1:
                return children[0]
            active = node["active_child_id"]
            return {
                **node,
                "children": children,
                "active_child_id": (
                    active if active in {child["id"] for child in children} else children[0]["id"]
                ),
            }
        first = remove(node["first"])
        second = remove(node["second"])
        if first is None:
            return second
        if second is None:
            return first
        return {**node, "first": first, "second": second}

    return normalize_layout({**normalized, "root": remove(normalized["root"])})
