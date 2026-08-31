"""Path-scoped edits to a JSON document whose schema this process does not know.

The device-settings store keeps seven of its nine domains **opaquely**: the
browser owns their schema, normalizes them, and is the only thing that can say
whether a given arrangement means anything (`settings_store.py`). That was fine
while the browser was the only editor. The configurator agent is a second one,
and it needs to change a command rail without the daemon learning what a rail is.

Whole-document replacement is the obvious way to do that and it is the wrong one.
A rail blob is twelve kilobytes of nested identifiers; asking a model to resend
all of it in order to delete four strings makes every byte it did not mean to
touch part of the blast radius, and the store cannot catch the damage because it
cannot tell a valid rail from a mangled one. The failure is silent and total: the
browser normalizes the wreckage rather than rejecting it, and someone's rail is
simply gone.

So a write names **what to change and nothing else**. Everything the operation
did not address is untouched by construction, which is a property of the shape of
the request rather than of the care taken in producing it - and it holds without
this module knowing a single thing about rails, sounds, or drawer tabs.

Four operations, chosen because they are what editing a list-of-things document
actually requires and because none of them can express "replace everything":

- ``set`` writes one value at one path.
- ``remove`` deletes one key or one element.
- ``remove_values`` deletes every element of an array equal to any of the values
  named. This is the one that carries most of the ergonomic weight: "take
  up/down/left/right out of this row" is one operation that is order-independent,
  idempotent, and cannot be thrown off by the removals shifting the indices of
  each other - which index-based removal cannot claim and gets wrong in exactly
  the case the caller is least likely to test.
- ``insert`` places a value into an array, relative to a value already in it or
  at an index.

Paths are JSON Pointer (RFC 6901) with one addition: a segment written
``[key=value]`` selects the element of an array whose ``key`` field equals
``value``. Positional paths into an array of records are legible only to whoever
counted, and they silently address a different record when the array is
reordered between the read and the write; naming the row by its own id cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The closed operation set. A caller naming anything else is refused rather than
#: ignored - a silently dropped operation reads as a write that did nothing.
OPERATIONS = ("set", "remove", "remove_values", "insert")

#: Ceiling on operations in one request. Generous next to any real edit, and it
#: exists so a malformed generation cannot spend the loop.
MAX_OPERATIONS = 64


class PatchError(ValueError):
    """A patch that cannot be applied, with the path that could not be resolved.

    A `ValueError` so the MCP layer's existing parameter-error path carries it,
    and the message always names the path: "the array is not there" and "the
    value is not in the array" are different problems and an agent that cannot
    tell them apart will retry the wrong one.
    """


@dataclass(frozen=True, slots=True)
class Selector:
    """``[key=value]`` - the element of an array whose ``key`` field is ``value``."""

    key: str
    value: str


def _unescape(segment: str) -> str:
    # RFC 6901: `~1` is `/` and `~0` is `~`, in that order.
    return segment.replace("~1", "/").replace("~0", "~")


def parse_path(path: str) -> list[str | int | Selector]:
    """A pointer string as the sequence of steps it names.

    An empty path is the document itself, which is a legal thing to *read* and
    never a legal thing to write: replacing the root is whole-document
    replacement wearing a pointer, which is the operation this module exists to
    not have.
    """
    if not isinstance(path, str):
        raise PatchError("path must be a string")
    if path == "":
        return []
    if not path.startswith("/"):
        raise PatchError(f"path must start with '/': {path!r}")
    steps: list[str | int | Selector] = []
    for raw in path.split("/")[1:]:
        segment = _unescape(raw)
        if segment.startswith("[") and segment.endswith("]") and "=" in segment:
            key, _, value = segment[1:-1].partition("=")
            if not key:
                raise PatchError(f"selector needs a key: {raw!r} in {path!r}")
            steps.append(Selector(key, value))
        elif segment.lstrip("-").isdigit():
            steps.append(int(segment))
        else:
            steps.append(segment)
    return steps


def _describe(steps: list[str | int | Selector]) -> str:
    parts = []
    for step in steps:
        if isinstance(step, Selector):
            parts.append(f"[{step.key}={step.value}]")
        else:
            parts.append(str(step))
    return "/" + "/".join(parts)


def _step_into(current: Any, step: str | int | Selector, walked: list[Any], path: str) -> Any:
    where = f"{_describe(walked)} in {path!r}"
    if isinstance(step, Selector):
        if not isinstance(current, list):
            raise PatchError(f"{where} is not an array, so [{step.key}=...] cannot select in it")
        for entry in current:
            if isinstance(entry, dict) and str(entry.get(step.key, "")) == step.value:
                return entry
        raise PatchError(f"no element at {where} has {step.key} == {step.value!r}")
    if isinstance(step, int):
        if not isinstance(current, list):
            raise PatchError(f"{where} is not an array, so index {step} cannot be used")
        if not -len(current) <= step < len(current):
            raise PatchError(f"index {step} is outside the {len(current)}-element array at {where}")
        return current[step]
    if not isinstance(current, dict):
        raise PatchError(f"{where} is not an object, so key {step!r} cannot be read from it")
    if step not in current:
        raise PatchError(f"{where} has no key {step!r}")
    return current[step]


def resolve(document: Any, path: str) -> Any:
    """The value a path names, or raise naming the step that failed."""
    steps = parse_path(path)
    current = document
    walked: list[Any] = []
    for step in steps:
        current = _step_into(current, step, walked, path)
        walked.append(step)
    return current


def _resolve_parent(
    document: Any, steps: list[str | int | Selector], path: str
) -> tuple[Any, str | int | Selector]:
    if not steps:
        raise PatchError("the document root cannot be edited; name a path inside it")
    current = document
    walked: list[Any] = []
    for step in steps[:-1]:
        current = _step_into(current, step, walked, path)
        walked.append(step)
    return current, steps[-1]


def _container_index(container: Any, step: str | int | Selector, path: str) -> int:
    """Where in an array a step points, for the operations that need a position."""
    if isinstance(step, Selector):
        for index, entry in enumerate(container):
            if isinstance(entry, dict) and str(entry.get(step.key, "")) == step.value:
                return index
        raise PatchError(f"no element in the array at {path!r} has {step.key} == {step.value!r}")
    if isinstance(step, int):
        if not -len(container) <= step < len(container):
            raise PatchError(
                f"index {step} is outside the {len(container)}-element array at {path!r}"
            )
        return step if step >= 0 else len(container) + step
    raise PatchError(f"{step!r} is a key, and the value at {path!r} is an array")


def _apply_set(document: Any, operation: dict[str, Any], path: str) -> str:
    if "value" not in operation:
        raise PatchError(f"set at {path!r} needs a value")
    container, last = _resolve_parent(document, parse_path(path), path)
    value = operation["value"]
    if isinstance(container, list):
        index = _container_index(container, last, path)
        before = container[index]
        container[index] = value
        return f"set {path} (was {_short(before)})"
    if not isinstance(container, dict) or isinstance(last, Selector):
        raise PatchError(f"the parent of {path!r} is not an object")
    existed = last in container
    before = container.get(last)
    container[str(last)] = value
    return f"set {path}" + (f" (was {_short(before)})" if existed else " (new key)")


def _apply_remove(document: Any, operation: dict[str, Any], path: str) -> str:
    container, last = _resolve_parent(document, parse_path(path), path)
    if isinstance(container, list):
        index = _container_index(container, last, path)
        removed = container.pop(index)
        return f"removed {path} ({_short(removed)})"
    if not isinstance(container, dict) or isinstance(last, Selector):
        raise PatchError(f"the parent of {path!r} is not an object")
    if last not in container:
        raise PatchError(f"{path!r} does not exist")
    removed = container.pop(str(last))
    return f"removed {path} ({_short(removed)})"


def _apply_remove_values(document: Any, operation: dict[str, Any], path: str) -> str:
    values = operation.get("values")
    if not isinstance(values, list) or not values:
        raise PatchError(f"remove_values at {path!r} needs a non-empty values array")
    target = resolve(document, path)
    if not isinstance(target, list):
        raise PatchError(f"{path!r} is not an array, so values cannot be removed from it")
    wanted = list(values)
    kept = [entry for entry in target if entry not in wanted]
    removed = [entry for entry in target if entry in wanted]
    absent = [entry for entry in wanted if entry not in target]
    if not removed:
        # Not an error - the operation is idempotent by design - but silence here
        # would let an agent report a change that did not happen.
        raise PatchError(
            f"none of {wanted!r} are in the array at {path!r}; it holds "
            f"{[_short(entry) for entry in target[:12]]}"
        )
    target[:] = kept
    note = f"removed {len(removed)} from {path}: {[_short(entry) for entry in removed]}"
    return note + (f" (not present: {absent!r})" if absent else "")


def _apply_insert(document: Any, operation: dict[str, Any], path: str) -> str:
    if "value" not in operation:
        raise PatchError(f"insert at {path!r} needs a value")
    target = resolve(document, path)
    if not isinstance(target, list):
        raise PatchError(f"{path!r} is not an array, so nothing can be inserted into it")
    anchors = [key for key in ("after", "before", "index") if key in operation]
    if len(anchors) > 1:
        raise PatchError(f"insert at {path!r} names {anchors}; use exactly one")
    value = operation["value"]
    if "after" in operation or "before" in operation:
        anchor = operation.get("after", operation.get("before"))
        if anchor not in target:
            raise PatchError(f"the anchor {anchor!r} is not in the array at {path!r}")
        at = target.index(anchor) + (1 if "after" in operation else 0)
    elif "index" in operation:
        raw = operation["index"]
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise PatchError(f"insert index at {path!r} must be an integer")
        at = max(0, min(len(target), raw if raw >= 0 else len(target) + raw))
    else:
        at = len(target)
    target.insert(at, value)
    return f"inserted {_short(value)} into {path} at {at}"


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


_APPLIERS = {
    "set": _apply_set,
    "remove": _apply_remove,
    "remove_values": _apply_remove_values,
    "insert": _apply_insert,
}


def apply_operations(document: Any, operations: Any) -> tuple[Any, list[str]]:
    """Apply every operation in order, or apply none of them.

    The copy is what makes that true: operations mutate a private deep copy and
    the caller only ever sees it if every one succeeded, so a batch whose third
    operation names a path that does not exist leaves the stored document exactly
    as it was rather than half-edited. Half-edited is the worst outcome available
    here - the document is opaque, so nobody downstream can tell.
    """
    import copy

    if not isinstance(operations, list) or not operations:
        raise PatchError("operations must be a non-empty array")
    if len(operations) > MAX_OPERATIONS:
        raise PatchError(f"at most {MAX_OPERATIONS} operations in one request")
    working = copy.deepcopy(document)
    notes: list[str] = []
    for position, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise PatchError(f"operation {position} is not an object")
        kind = str(operation.get("op") or "")
        if kind not in _APPLIERS:
            raise PatchError(
                f"operation {position} is {kind!r}; use one of {', '.join(OPERATIONS)}"
            )
        path = operation.get("path")
        if not isinstance(path, str):
            raise PatchError(f"operation {position} needs a string path")
        try:
            notes.append(_APPLIERS[kind](working, operation, path))
        except PatchError as exc:
            raise PatchError(f"operation {position} ({kind} {path}): {exc}") from None
    if not isinstance(working, dict):
        raise PatchError("the result is not an object; a settings domain must stay one")
    return working, notes
