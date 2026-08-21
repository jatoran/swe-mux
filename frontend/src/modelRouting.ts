/**
 * Which model every model-backed feature calls, and where the control that decides
 * it lives.
 *
 * The controls stay with the features they configure - the assistant's model beside
 * the assistant's budget and trust policy, the scan timeline's beside its caps -
 * because a feature is set up in one pass, and a shared "all the models" form would
 * split every one of those passes across two tabs. The cost of that choice is what
 * this table pays back: with the pickers scattered, there was nowhere to see what
 * the install as a whole is spending model calls on.
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
    where: 'Automation → Scan timeline',
    target: { tab: 'automation', setting: 'scan_timeline_model' },
    note: 'Samples continuously over long transcript slices, so it has to be cheap at volume and reliable at structured output at once.',
  },
  {
    feature: 'Attention narration', key: 'attention_narration_model', kind: 'override',
    fallback: 'openrouter_cheap_model', where: 'Automation → Attention',
    target: { tab: 'automation', setting: 'attention_narration_model' },
  },
  {
    feature: 'Spoken summary', key: 'tts_summary_model', kind: 'override',
    fallback: 'openrouter_cheap_model', where: 'Voice → Spoken summary',
    target: { tab: 'voice', setting: 'tts_summary_model' },
  },
  {
    feature: 'Mux assistant', key: 'assistant_model', kind: 'pinned',
    where: 'Voice → Mux assistant',
    target: { tab: 'voice', setting: 'assistant_model' },
    note: 'An agentic tool-calling loop: a model that only sometimes emits a well-formed call fails as a broken assistant rather than a cheap one.',
  },
  {
    feature: 'Project context card', key: 'project_card_model', kind: 'override',
    fallback: 'openrouter_cheap_model', where: 'Automation → Budgets and execution',
    target: { tab: 'automation', setting: 'project_card_model' },
    note: 'Beside the budget and the per-build token ceilings that bound one rebuild of the card.',
  },
]

/**
 * How a custom endpoint changes every row above.
 *
 * A local server serves one model, and every id in the table names an OpenRouter route
 * that server has never heard of, so the client redirects all of them to
 * `custom_llm_model`. The table is kept rather than emptied - these settings still hold
 * their values and apply again the moment the provider is switched back - but a summary
 * that went on listing seven distinct model ids while one model answered all of them
 * would be the most misleading thing on the screen, which is exactly the failure the
 * index exists to prevent.
 */
export type ProviderOverride = { provider: string; model: string } | null

export function customProviderOverride(
  config: { llm_provider?: string; custom_llm_model?: string },
): ProviderOverride {
  if (config.llm_provider !== 'custom') return null
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
