/**
 * The "which model does each feature call" table in Settings → Accounts, and the
 * one place any of them is edited.
 *
 * It was a read-only inventory beside seven pickers scattered across four tabs. The
 * reasoning for that split, and why changing provider is the operation that broke
 * it, is in `modelRouting.ts`; this is its presentation. Two controls writing one
 * config key is still how a panel starts disagreeing with itself, so the pickers
 * moved here rather than being duplicated - each feature tab now shows a read-only
 * row that links back.
 *
 * Three things each row has to say, because with one endpoint swappable underneath
 * them any of the three can be the reason a feature is not working:
 *
 * - **What it will call**, resolved through the fallback chain, not just what this
 *   key holds.
 * - **Whether the current endpoint has it.** A model configured against OpenRouter
 *   and missing from a proxy's catalog fails at call time as a provider error;
 *   here it is a flag on the row, before the call.
 * - **Whether the endpoint is answering all of them with one model**, which is
 *   true for a catalog-less server and false for every other endpoint.
 */
import type { ModelOption } from './modelFilter'
import { ModelPicker } from './ModelPicker'
import {
  MODEL_ROUTES, resolveRoute, type ModelRoutingConfig, type ProviderOverride,
} from './modelRouting'
import { modelMetaLabel } from './modelPricing'

type Props = {
  draft: ModelRoutingConfig
  /** The active endpoint's cached catalog, for the price and the presence check. */
  catalog: ModelOption[]
  /**
   * Set only while the endpoint serves no catalog, in which case every row resolves
   * to that endpoint's single model. Passed in rather than derived here so this
   * component keeps knowing nothing about providers.
   */
  override?: ProviderOverride
  /**
   * Whether the catalog is worth checking a model against. A `none` endpoint has an
   * empty list for a reason, and flagging all seven rows as "not in this catalog"
   * when there is no catalog would be noise rather than a finding.
   */
  catalogKnown?: boolean
  onChange: (key: keyof ModelRoutingConfig, value: string) => void
}

export function ModelRoutingSummary(
  { draft, catalog, override = null, catalogKnown = false, onChange }: Props,
) {
  const byId = new Map(catalog.map(model => [model.id, model]))
  return <ul class="model-routing">
    {MODEL_ROUTES.map(route => {
      const { model, inherited } = resolveRoute(route, draft, override)
      const entry = model ? byId.get(model) : undefined
      const price = entry ? modelMetaLabel(entry) : null
      // Only a model this row actually chose can be missing in a way worth saying.
      // An inherited value is reported on the row that owns it, once.
      const missing = Boolean(
        catalogKnown && model && !inherited && !entry,
      )
      // The mark the deep link from a feature tab reveals. On the row rather
      // than on the read-out inside it, so arriving here lands on the control
      // that changes the value and not on the sentence describing it.
      return <li key={route.key} data-setting={route.key}>
        <div class="model-routing-head">
          <strong>{route.feature}</strong>
          <span class={`model-routing-kind model-routing-${route.kind}`}>{route.kind}</span>
        </div>
        <ModelPicker
          id={`${route.key}-picker`}
          value={draft[route.key] || ''}
          options={catalog}
          emptyLabel={route.fallback
            ? `Use the ${route.fallback === 'openrouter_cheap_model' ? 'cheap' : 'standard'} model…`
            : 'Select exact model…'}
          required={route.kind === 'pinned'}
          onChange={value => onChange(route.key, value)}
        />
        <div class="model-routing-model">
          {model
            // The exact id, not a display label: this row answers "which model is
            // that", and it is also the value the control above holds.
            ? <code title={model}>{model}</code>
            : <em>{route.kind === 'pinned' ? 'not set — this feature cannot run' : 'not set'}</em>}
          {inherited && <span class="model-routing-inherited">{override ? 'endpoint' : 'inherited'}</span>}
          {price && <span class="model-routing-price">{price}</span>}
          {missing && <span class="model-routing-missing" title="This endpoint's catalog does not list this model. The call may still work if the endpoint resolves the id upstream, but nothing here can price it or confirm it.">not in this catalog</span>}
        </div>
        {route.note && <small>{route.note}</small>}
      </li>
    })}
  </ul>
}
