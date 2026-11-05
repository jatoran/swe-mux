# Localization catalogs

## Adding translations

Fill the matching keys in these UTF-8 JSON objects:

| English source | Chinese target |
|---|---|
| `frontend/src/localization/catalogs/en.json` | `frontend/src/localization/catalogs/zh-Hans.json` |
| `src/swe_mux/assets/locales/en.json` | `src/swe_mux/assets/locales/zh-Hans.json` |

Keep ids unchanged.
Both target catalogs must cover every English key with a nonempty value before tests or builds pass.
The application retains English fallback for runtime recovery.
Keep program identifiers, filesystem paths, model names, product names, command examples, and recognized voice-command phrases unchanged.
Translate explanations around those tokens; never rewrite user content.
`frontend/src/localization/verbatim.json` protects technical examples and syntax whose Chinese value must equal the English source.
Add an id there when a catalog entry is a literal command or configuration example rather than prose.

The frontend uses ICU MessageFormat.
Keep every argument occurrence, format style, rich-content slot, and conditional branch key.
A translation may reorder arguments and change wording within plural or select branches.
Chinese count labels omit English plural suffixes while preserving the original select structure.
`catalogContract.ts` validates argument multiplicity and branch membership, so dropping one occurrence cannot silently pass.
Apostrophes and literal braces follow ICU escaping rules.
Rich slots represent existing controls, links, or emphasis; translation text is never parsed as HTML.

The backend uses Python format strings with named arguments.
Keep each argument occurrence, name, conversion, and format specification.
Double literal braces.
Do not translate protocol keys or change the structures surrounding a message.

`frontend/src/localization/sources.json` locates frontend references and records bounded argument context.
Regenerate it with `node --experimental-strip-types frontend/scripts/check-localization.mjs --report` after moving code.
The report is reference material; catalogs are the source of copy.

## Chinese terminology

| English | Chinese |
|---|---|
| Project / session / conversation | 项目 / 会话 / 对话 |
| Agent / harness | 智能体 / 智能体工具 |
| Worktree / trunk / land | 工作树 / 主干 / 合入 |
| Daemon / supervisor | 守护进程 / 监督进程 |
| Approval / grant | 审批 / 授权 |
| Prompt / composer | 提示词 / 输入框 |
| Turn / context compaction | 轮次 / 上下文压缩 |
| Token count / authentication token | Token / 令牌 |

Voice recognition remains English; translated UI descriptions do not add Chinese voice commands.

## Adding or changing UI copy

Add matching English and Chinese messages, then reference their shared id through `t` or `rich`.
New ids may be descriptive; existing ids remain stable when English wording changes.
TypeScript derives valid ids from the source catalog.

```tsx
// Correct: one complete message, with values whose order the translator controls.
t('queue.itemCount', { count })

// Incorrect: English grammar assembled around translated fragments.
count + ' ' + t('queue.item') + (count === 1 ? '' : 's')
```

Keep complete sentences together, including conditional words.
Use ICU plural or select expressions instead of concatenating English suffixes.
Use `rich` when a sentence contains an existing VNode.
Preserve the caller's event handlers and the identity of interactive children.

Do not evaluate translated copy once at module import.
Use an accessor for a descriptor label or a function for a shared message.
A memoized derived label must depend on locale and catalog revision.
Pure callback registries remain independent of locale.

Use `numberFormatter` and `dateTimeFormatter` for cached presentation formatters.
They read the active language when formatting.
Machine timestamps, API values, command syntax, and saved ids retain their original formats.

Backend-facing labels and errors use `ui_message`.
Keep the resulting value until `json_response` or the native/push presentation boundary.
Use `error_text` when transferring an exception's catalog-backed message into an API error body.
Do not add localization dependencies to the PTY supervisor, framed IPC, terminal core, or gateway closure.

## Generated help

`frontend/scripts/build-help-content.mts` owns help messages derived from feature documentation.
Run `node frontend/scripts/build-help-content.mts` after changing one of its selected source sections.
It updates both the generated references and the English help catalog.
Do not hand-edit `helpContent.generated.ts`.

## Verification

`npm run check:localization` checks product display literals, message ids, ICU syntax, call arguments, complete Chinese coverage, formatting contracts, and verbatim examples.
It runs before frontend tests and production builds.
The two language self-names, decorative glyphs, and explicitly identified development-only surfaces are intentional exclusions.
`tests/test_localization.py` checks backend catalog references and placeholder parity as well as persistence and presentation behavior.

The renderer fixture installs the shipped Chinese catalog, so browser checks exercise production translations.
It exercises selection, persistence, failure recovery, Settings navigation and search, and terminal preservation.
Run it on a confirmed-free loopback port using the repository's renderer workflow.

Legacy source-contract tests expand catalog expressions into English before inspecting wiring.
They are supplemented by runtime localization tests; the expansion helpers are not part of the application.
