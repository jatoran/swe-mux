/**
 * Which model every model-backed feature calls, and the one place that decides it.
 *
 * The controls used to stay with the features they configure - the assistant's model
 * beside the assistant's budget, the scan timeline's beside its caps - on the rule
 * that a feature is set up in one pass. That rule optimised the wrong axis. Changing
 * *provider* is the operation that touches every one of these at once, and under it
 * the per-feature layout meant four tabs of hunting to find out whether anything had
 * broken - with no screen anywhere answering "what does this install call, and does
 * the endpoint I just switched to even have it".
 *
 * So this table is the editor now, rendered once under Accounts → Models, and each
 * feature tab keeps a read-only row naming its resolved model with a link back here.
 * That preserves the half of the old rule worth keeping - standing on the voice tab
 * and wanting to know what it calls - without a second control writing the same key.
 *
 * It lives apart from `ModelRoutingSummary.tsx` for the reason `settingsTabs.ts`
 * lives apart from `Settings.tsx`: none of it needs a renderer, and "which model
 * does this feature actually use" is worth asserting without mounting a panel.
 *
 * Three kinds of choice appear, and the distinction is not cosmetic - it decides
 * what a blank value means:
 *
 *   - **routed** - the shared defaults (`cheap`, `standard`) everything else is
 *     expressed against.
 *   - **override** - blank means "use the routed one". Nothing breaks if it is never
 *     set, so the summary reports what it inherits rather than an empty cell.
 *   - **pinned** - the daemon rejects a blank value (`validate_config`), because the
 *     feature needs a capability the routed pair does not promise. A pin is a
 *     requirement, not a preference.
 */
import type { SettingsTab } from './settingsTabs.ts'

/**
 * The config keys this view reads, declared structurally rather than by importing
 * the panel's `Config`: the summary depends on seven model ids, not on the whole
 * settings shape.
 */
export type ModelRoutingConfig = {
  openrouter_cheap_model: string
  openrouter_standard_model: string
  scan_timeline_model: string
  attention_narration_model: string
  tts_summary_model: string
  assistant_model: string
  project_card_model: string
}

export type ModelRouteKind = 'routed' | 'override' | 'pinned'

type RoutedKey = 'openrouter_cheap_model' | 'openrouter_standard_model'

export type ModelRoute = {
  feature: string
  key: keyof ModelRoutingConfig
  kind: ModelRouteKind
  /** The routed key a blank value falls through to, where one exists. */
  fallback?: RoutedKey
  /** Where the control lives, in the words the tab list uses. */
  where: string
  /** The control to open. Absent where the setting has none in the panel. */
  target?: { tab: SettingsTab; setting: string }
  /** Why a pin cannot inherit, or why a row has no control. */
  note?: string
}

/**
 * The routing table. Ordered as the fallbacks resolve - the routed pair first, then
 * everything expressed against them - so no row explains itself in terms of one
 * further down the list.
 */
export const MODEL_ROUTES: readonly ModelRoute[] = [
  {
    feature: 'Cheap model', key: 'openrouter_cheap_model', kind: 'routed',
    where: 'Accounts → Models',
    target: { tab: 'accounts', setting: 'openrouter_cheap_model' },
    note: 'Observers, conversation titles and summaries, and every override left blank.',
  },
  {
    feature: 'Standard model', key: 'openrouter_standard_model', kind: 'routed',
    fallback: 'openrouter_cheap_model', where: 'Accounts → Models',
    target: { tab: 'accounts', setting: 'openrouter_standard_model' },
    note: 'Observer retries the cheap model failed, and manually re-run observer batches.',
  },
  {
    feature: 'Scan timeline', key: 'scan_timeline_model', kind: 'pinned',
    where: 'Accounts → Models',
    target: { tab: 'accounts', setting: 'scan_timeline_model' },
    note: 'Samples continuously over long transcript slices, so it has to be cheap at volume and reliable at structured output at once.',
  },
  {
    feature: 'Attention narration', key: 'attention_narration_model', kind: 'override',
    fallback: 'openrouter_cheap_model', where: 'Accounts → Models',
    target: { tab: 'accounts', setting: 'attention_narration_model' },
  },
  {
    feature: 'Spoken summary', key: 'tts_summary_model', kind: 'override',
    fallback: 'openrouter_cheap_model', where: 'Accounts → Models',
    target: { tab: 'accounts', setting: 'tts_summary_model' },
  },
  {
    feature: 'Mux assistant', key: 'assistant_model', kind: 'pinned',
    where: 'Accounts → Models',
    target: { tab: 'accounts', setting: 'assistant_model' },
    note: 'An agentic tool-calling loop: a model that only sometimes emits a well-formed call fails as a broken assistant rather than a cheap one.',
  },
  {
    feature: 'Project context card', key: 'project_card_model', kind: 'override',
    fallback: 'openrouter_cheap_model', where: 'Accounts → Models',
    target: { tab: 'accounts', setting: 'project_card_model' },
    note: 'Beside the budget and the per-build token ceilings that bound one rebuild of the card.',
  },
]

/**
 * When a custom endpoint collapses every row above onto one model, and when it does not.
 *
 * A server with **no catalog** serves one model, and every id in the table names a
 * route it has never heard of, so the client redirects all of them to
 * `custom_llm_model`. The table is kept rather than emptied - these settings still hold
 * their values and apply again the moment the provider is switched back - but a summary
 * that went on listing seven distinct model ids while one model answered all of them
 * would be the most misleading thing on the screen.
 *
 * An endpoint that **serves a catalog** collapses nothing. There is something to choose
 * between, the daemon stops redirecting (`llm_endpoint.custom_endpoint`), and every one
 * of these settings reaches the wire exactly as written. Reporting the collapse anyway
 * is the same failure in the opposite direction, and a worse one: it tells you the
 * models you carefully chose have been silently replaced when they have not, which is
 * a reason not to switch provider at all.
 *
 * `hasCatalog` therefore comes from the measured capability record rather than from the
 * provider id - the whole point being that `custom` no longer implies anything.
 */
export type ProviderOverride = { provider: string; model: string } | null

export function customProviderOverride(
  config: { llm_provider?: string; custom_llm_model?: string },
  hasCatalog = false,
): ProviderOverride {
  if (config.llm_provider !== 'custom' || hasCatalog) return null
  return { provider: 'custom', model: (config.custom_llm_model || '').trim() }
}

/** What a route resolves to, and whether that value is its own or inherited. */
export type ResolvedRoute = {
  route: ModelRoute
  /** The exact model id the feature will call. Empty when nothing resolves. */
  model: string
  inherited: boolean
}

export function resolveRoute(
  route: ModelRoute,
  config: ModelRoutingConfig,
  override: ProviderOverride = null,
): ResolvedRoute {
  // The override wins over both the pin and the fallback, because it is what the client
  // will actually send. `inherited` is true for every row under it: none of these routes
  // chose this model, they all fell through to the endpoint's single one.
  if (override) return { route, model: override.model, inherited: true }
  const configured = (config[route.key] || '').trim()
  if (configured) return { route, model: configured, inherited: false }
  const inherited = route.fallback ? (config[route.fallback] || '').trim() : ''
  return { route, model: inherited, inherited: Boolean(inherited) }
}

export const resolveRoutes = (
  config: ModelRoutingConfig,
  override: ProviderOverride = null,
): ResolvedRoute[] => MODEL_ROUTES.map(route => resolveRoute(route, config, override))
