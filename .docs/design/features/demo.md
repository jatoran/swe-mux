# Interactive website demo

The website and full-screen demo use the production workspace components with a simulated backend.
Every session, path, response, quota and timing is fixture data.
Nothing connects to a real daemon or model provider.

## Languages

English and Simplified Chinese share one build.
The full-screen language picker, embedded website controls, captions, fixture conversations and simulated terminal prompts use the demo catalogs.
The application itself continues to use its production catalogs.
The website's remaining editorial text is English, with prominent links to the Chinese README and demo.

Use `/demo/?lang=zh-Hans` for a Chinese link or `?lang=en` for English.
An explicit URL choice wins at startup; otherwise the saved or supported browser preference applies.
Language changes use the shared demo store and propagate between desktop and phone frames.
They stop the current walkthrough so a new run resolves its captions consistently.
Switching language preserves existing sessions, terminal instances, user edits and the selected keymap.
Existing conversation text stays as originally created; reset starts a freshly localized fixture.
The demo state version changes when seeded content needs replacement.

Scenario identities, dialog identities, tab IDs, command IDs and shortcut chords are independent of displayed text.
Only labels and descriptions translate.
Voice-command examples keep their English phrases and explicitly describe that limitation.
The static demo does not register the production service worker or request real push subscriptions.

## Source and build

- `frontend/src/demo/catalogs/en.json` and `zh-Hans.json` contain demo copy.
- `messages.ts` uses the application's active locale and ICU formatting.
- `languageBootstrap.ts` resolves the initial choice before fixtures load.
- `languageControls.ts` synchronizes selection and emits the website integration events.
- `terminalWidth.ts` measures terminal cells and preserves grapheme boundaries for Chinese text and emoji.
- `frontend/scripts/gen_keymap_fixture.py` generates both keymap fixtures and Chinese presentation patches from the real backend.
- `frontend/scripts/build-demo-copy.mjs` generates `site/demo-language.json` from the same catalog and scenario definitions.
- `npm --prefix frontend run build:demo` produces the committed `site/demo/` bundle.

The demo catalog gate checks complete English/Chinese coverage, ICU contracts, message references and uncataloged visible controls.
Run `frontend/test/renderer/demo-localization.spec.ts` on a confirmed-free renderer port for switching, cross-frame behavior, composition and walkthrough tests.

## Promotional captures

`node scripts/capture-demo.mjs --scenario queue --surface phone --language zh-Hans --out ../.trash/demo-capture` runs from `frontend/`.
The same command with `--surface desktop` records the desktop layout.
It captures the built static bundle, writes beat stills, a WebM recording, Chinese SRT captions, diagnostics and a manifest containing the fixture fingerprint.
`--check` runs twice and verifies determinism without recording video.
Existing output is archived into project `.trash`.

Keep the simulated-session disclosure beside published media.
Captions describe the scripted workflow; they are not benchmark results.
Encode distribution MP4s from these recordings and retain the SRT and capture manifest with them in the private marketing kit.
