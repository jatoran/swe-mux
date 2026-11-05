"""Catalog-backed human-facing text; protocol identifiers and logs remain stable English."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
UI_LOCALE: ContextVar[str] = ContextVar("ui_locale", default="en")
_LOCALES = Path(__file__).parent / "assets" / "locales"


@lru_cache(maxsize=2)
def catalog(locale: str) -> dict[str, str]:
    path = _LOCALES / f"{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise ValueError(f"Invalid UI catalog: {path.name}")
    return data


def resolve_ui_locale(preference: str, languages: str = "") -> str:
    if preference in {"en", "zh-Hans"}:
        return preference
    for item in languages.split(","):
        language = item.split(";")[0].strip().lower()
        if language in {"zh", "zh-cn", "zh-sg"} or language.startswith("zh-hans"):
            return "zh-Hans"
        if language == "en" or language.startswith("en-"):
            return "en"
    return "en"


def _format_text(source: str, values: dict[str, Any]) -> str:
    positional = [values[str(index)] for index in range(len(values)) if str(index) in values]
    return source.format(*positional, **values)


class UiMessage(str):
    """A string-compatible message retaining its identity until the presentation boundary."""

    message_id: str
    values: dict[str, Any]

    def __new__(cls, message_id: str, values: dict[str, Any] | None = None) -> UiMessage:
        parameters = values or {}
        source = catalog("en")[message_id]
        try:
            text = _format_text(source, parameters)
        except (KeyError, ValueError, IndexError):
            text = source
        result = super().__new__(cls, text)
        result.message_id = message_id
        result.values = parameters
        return result

    def __getnewargs_ex__(self) -> tuple[tuple[str, dict[str, Any]], dict[str, Any]]:
        return (self.message_id, self.values), {}

    def format(self, *args: Any, **kwargs: Any) -> UiMessage:
        return UiMessage(
            self.message_id,
            {**self.values, **{str(index): value for index, value in enumerate(args)}, **kwargs},
        )


def ui_message(message_id: str, **values: Any) -> UiMessage:
    return UiMessage(message_id, values)


def error_text(error: BaseException) -> str:
    """Keep a domain message attached to an exception instead of losing it through str()."""
    message = getattr(error, "message", None)
    if isinstance(message, UiMessage):
        return message
    if len(error.args) == 1 and isinstance(error.args[0], UiMessage):
        return error.args[0]
    return str(error)


def render_message(message: UiMessage, locale: str | None = None) -> str:
    selected = locale or UI_LOCALE.get()
    if selected not in {"en", "zh-Hans"}:
        selected = "en"
    source = catalog("en")[message.message_id]
    translated = catalog(selected).get(message.message_id, source)
    values = {
        key: render_message(value, selected) if isinstance(value, UiMessage) else value
        for key, value in message.values.items()
    }
    try:
        return _format_text(translated, values)
    except (KeyError, ValueError, IndexError, TypeError):
        log.warning("ui_message_fallback locale=%s message_id=%s", selected, message.message_id)
        return str(message)


def localize_payload(value: Any, locale: str | None = None) -> Any:
    """Only explicitly marked UI strings translate; state names, ids, and user text do not."""
    if isinstance(value, UiMessage):
        return render_message(value, locale)
    if isinstance(value, dict):
        return {key: localize_payload(item, locale) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [localize_payload(item, locale) for item in value]
    return value


@lru_cache(maxsize=8)
def _native_preference(path: str, modified_ns: int) -> str:
    del modified_ns
    import tomllib

    try:
        preference = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("ui_language", "")
        return preference if preference in {"en", "zh-Hans"} else ""
    except (OSError, ValueError, TypeError):
        log.warning("native_ui_language_read_failed")
        return ""


def native_ui_locale(config_path: Path | None) -> str:
    """The tray lives beyond daemon restarts, so menu reads follow the saved preference."""
    import locale

    preference = ""
    if config_path is not None:
        try:
            preference = _native_preference(str(config_path), config_path.stat().st_mtime_ns)
        except OSError:
            pass
    system_language = (locale.getlocale()[0] or "").replace("_", "-")
    return resolve_ui_locale(preference, system_language)


def native_label(message_id: str, config_path: Path | None) -> Any:
    """pystray requests callable menu labels whenever it opens the menu."""

    def label(item: Any) -> str:
        del item
        return render_message(ui_message(message_id), native_ui_locale(config_path))

    return label


_native_config_path: Path | None = None


def configure_native_language(config_path: Path | None) -> None:
    global _native_config_path
    _native_config_path = config_path


def native_text(value: str) -> str:
    if isinstance(value, UiMessage):
        return render_message(value, native_ui_locale(_native_config_path))
    return value
