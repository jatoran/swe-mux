/**
 * The read-only "where each model is used" inventory in Settings → Accounts.
 *
 * The table it renders, and the rule for resolving an inherited value, are
 * `modelRouting.ts`; this is only its presentation. It is deliberately a summary and
 * not a second editor: two controls writing one config key is how a panel starts
 * disagreeing with itself, so each row states the resolved value and hands off to
 * the control that owns it.
 */
import type { ModelOption } from './modelFilter'
import { MODEL_ROUTES, resolveRoute, type ModelRoutingConfig } from './modelRouting'
import { modelMetaLabel } from './modelPricing'
import type { SettingsTab } from './settingsTabs'

type Props = {
  draft: ModelRoutingConfig
  /** The cached OpenRouter catalog, for the price beside each resolved model. */
  catalog: ModelOption[]
  onOpen: (tab: SettingsTab, setting: string) => void
}

export function ModelRoutingSummary({ draft, catalog, onOpen }: Props) {
  const byId = new Map(catalog.map(model => [model.id, model]))
  return <ul class="model-routing">
    {MODEL_ROUTES.map(route => {
      const { model, inherited } = resolveRoute(route, draft)
      const entry = model ? byId.get(model) : undefined
      const price = entry ? modelMetaLabel(entry) : null
      const target = route.target
      return <li key={route.key}>
        <div class="model-routing-head">
          <strong>{route.feature}</strong>
          <span class={`model-routing-kind model-routing-${route.kind}`}>{route.kind}</span>
        </div>
        <div class="model-routing-model">
          {model
            // The exact id, not a display label: this row answers "which model is
            // that", and it is also the value the linked control holds.
            ? <code title={model}>{model}</code>
            : <em>{route.kind === 'pinned' ? 'not set — this feature cannot run' : 'not set'}</em>}
          {inherited && <span class="model-routing-inherited">inherited</span>}
          {price && <span class="model-routing-price">{price}</span>}
        </div>
        {route.note && <small>{route.note}</small>}
        <div class="model-routing-where">
          {target
            ? <button type="button" onClick={() => onOpen(target.tab, target.setting)}>{route.where}</button>
            : <span>{route.where}</span>}
        </div>
      </li>
    })}
  </ul>
}
