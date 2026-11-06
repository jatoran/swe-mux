# UI localization

## What it is

One application supports English (`en`) and Simplified Chinese (`zh-Hans`).
Both catalogs ship with the application; translation requires no network service or separate installer.
Both catalogs contain complete Simplified Chinese copy, including help, backend errors, and native notifications.
Release checks require every English key to have a Chinese value with the same formatting contract.
Runtime English fallback remains available for damaged or partially installed catalogs.

Application chrome, setup, Settings, help, accessible labels, display statuses, backend messages, native dialogs, and push copy use catalogs.
Terminal output, user-authored names and content, provider output, command syntax, protocol values, and voice recognition are not translated.
The standalone voice benchmark is development material, outside the product-copy guard.
The marketing demo has separate English and Chinese catalogs and its own guard; see [Interactive website demo](demo.md).
Command labels, groups, shipped keymap descriptions and conflict warnings use backend catalogs while command IDs and chords stay unchanged.

## Preference and selection

`Config.ui_language` is an ordinary hot-applied setting with three accepted stored values:

| Value | Behavior |
|---|---|
| `""` | Follow the client's supported language preference until an explicit choice is saved. |
| `en` | English on every connected client. |
| `zh-Hans` | Simplified Chinese on every connected client, with English fallback. |

Setup and Settings → General expose the same English / 简体中文 selector.
An explicit selection uses `PATCH /api/config` and the ordinary `configuration_changed` event.
The selection survives reloads and applies across desktop and phone.
Changing it does not restart the daemon, remount terminals, or discard a Settings draft.
A failed save keeps the previous active language and exposes retryable feedback.

Before configuration arrives, the browser uses its last cached choice or `navigator.languages`.
The daemon's saved preference is authoritative.
Automatic detection accepts Simplified Chinese tags and English tags; unsupported preferences fall back to English.
Traditional Chinese is not a separate supported locale.

## Rendering and identity

The frontend uses ICU messages with named values and caller-owned rich content.
Translations can reorder values and use plural or select expressions.
Translated text is rendered as text; catalogs cannot introduce executable HTML.

A shared Preact signal refreshes mounted consumers.
Module-level display tables use accessors, and shared message functions resolve text when called.
Cached date and number formatters follow the active locale.
Settings retains status message render functions so existing status text changes language without changing its busy markers.
Settings search accepts Unicode letters and numbers and invalidates language-dependent caches.

Message ids, setting keys, page ids, command ids, and protocol status values remain stable.
Settings headings carry a message identity so navigation never derives a durable key from Chinese text.
Status codes receive translated labels only where displayed.

Backend `UiMessage` values retain their id and parameters while remaining string-compatible English values for domain logic and diagnostics.
The HTTP presentation boundary translates marked values under a request-scoped locale.
Live event messages read the current preference without reconnecting their socket.
Historical records retain their original recorded content.
Ordinary strings and user data remain untouched.
Localized conditional responses use distinct validators.
Native menus read the saved preference directly when opened, including successive changes with identical filesystem timestamps.
Push delivery reads the preference when sending.

## Diagnostics

Language writes enter the existing configuration log.
The frontend reports bounded, content-free localization events to `POST /api/diagnostics/localization`.
The endpoint records operation, locale, message id, reason, and request correlation in rotating daemon logs.
Neither interpolation values nor displayed content are accepted.
Missing translations are expected fallback, not one warning per message.
Malformed messages are reported and fall back safely.

## Key files

- `frontend/src/localization/index.ts`: locale resolution, reactive message formatting, fallback, and formatters.
- `frontend/src/localization/codeLabels.ts`: labels for displayed protocol values.
- `frontend/src/localization/catalogs/en.json` and `zh-Hans.json`: frontend catalogs.
- `frontend/src/LanguagePicker.tsx`: shared selection control.
- `src/swe_mux/ui_messages.py`: backend and native presentation.
- `src/swe_mux/assets/locales/en.json` and `zh-Hans.json`: backend catalogs.
- `frontend/scripts/check-localization.mjs`: UI-copy, id, ICU, and parameter checks.
- `frontend/test/renderer/localization.spec.ts` and `tests/test_localization.py`: behavior and boundary tests.

Translation workflow: [Frontend localization](../../technical/frontend/localization.md).
