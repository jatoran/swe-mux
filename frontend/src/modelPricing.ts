import { t } from './localization/index.ts'
/**
 * Price and capacity facts for one OpenRouter catalog entry, rendered for a
 * chooser rather than for a ledger.
 *
 * These numbers arrive already: `OpenRouterClient.models()` copies `pricing.prompt`
 * and `pricing.completion` into every cached entry as `prompt_price` /
 * `completion_price`, and the cache is handed to the browser verbatim by
 * `GET /api/automation/provider`. Nothing here fetches or derives anything - it
 * only decides how a number that the picker already holds should read.
 *
 * The unit conversion is the whole reason this is not inline formatting.
 * OpenRouter quotes **USD per token**, which for a cheap model is a figure like
 * `0.00000008`: printed as-is it is unreadable, and printed at two decimals it is
 * `$0.00` for every model in the catalog, which is worse than printing nothing.
 * Every figure here is therefore per *million* tokens, the unit model pricing is
 * quoted in everywhere else, and the multiplier is applied in one place.
 *
 * Four values are not prices and must not be formatted as one:
 *
 *   - `null` / absent - the catalog entry had no parseable pricing. Renders as
 *     nothing, never as `$0.00`, because "free" and "unknown" are opposite
 *     answers to "what will this cost me".
 *   - `0` - genuinely free. Only the word `free` when *both* sides are zero; a
 *     model that is free to prompt and paid to complete is a pair, not a
 *     giveaway, so each side keeps its figure.
 *   - negative - OpenRouter's marker for an entry whose price is decided at
 *     request time (the auto-routers). `variable` says that; a formatted
 *     negative dollar amount would claim a credit.
 *   - positive but smaller than the last printable digit - rounds to `$0.00` and
 *     would read as free, so it renders as `<$0.001`.
 */

/**
 * The catalog fields this module reads. Declared structurally rather than
 * importing `ModelOption`, so `modelFilter.ts` may depend on the shape without
 * either module owning the other.
 */
export type ModelPricingFacts = {
  /** USD per input token, as OpenRouter reports it. */
  prompt_price?: number | null
  /** USD per output token, as OpenRouter reports it. */
  completion_price?: number | null
  /** USD per cached input token. Absent means this model does not cache at all. */
  cache_read_price?: number | null
  /** USD per token written into the cache. Above `prompt_price` is a write premium. */
  cache_write_price?: number | null
  context_length?: number | null
}

const PER_MILLION = 1_000_000

/**
 * The smallest per-million figure that survives formatting. Anything under it
 * rounds to `$0.00`, which is the one rendering that actively misinforms.
 */
const SMALLEST_PRINTABLE = 0.001

/**
 * Two decimals is the money reader's default and three is the tail cheap models
 * actually live in ($0.015/M is a real price). Past $10/M the third decimal is
 * noise, so it is dropped rather than padded.
 */
const usd = (perMillion: number): string => perMillion.toLocaleString('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: perMillion >= 10 ? 2 : 3,
})

/** One side of the pair, per million tokens. `null` when there is no price to state. */
export function perMillionTokens(price: number | null | undefined): string | null {
  if (price === null || price === undefined || !Number.isFinite(price)) return null
  if (price < 0) return 'variable'
  if (price === 0) return '$0.00'
  const perMillion = price * PER_MILLION
  return perMillion < SMALLEST_PRINTABLE ? '<$0.001' : usd(perMillion)
}

/**
 * Input and output price as one compact cell: `$0.08 / $0.30 per M`.
 *
 * `null` when neither side is known, so a caller renders nothing rather than an
 * empty pair. A half-known pair keeps `?` for the missing side - the known half
 * is still the answer to "is this the cheap one".
 */
export function formatModelPricing(model: ModelPricingFacts): string | null {
  const input = perMillionTokens(model.prompt_price)
  const output = perMillionTokens(model.completion_price)
  if (input === null && output === null) return null
  if (model.prompt_price === 0 && model.completion_price === 0) return t("modelPricing.ad95d5fa65")
  return t("modelPricing.01d54c8959", { value1: input ?? '?', value2: output ?? '?' })
}

/** Context window as a reader states it: `128K`, `1M`. `null` when unstated. */
export function formatContextLength(tokens: number | null | undefined): string | null {
  if (tokens === null || tokens === undefined || !Number.isFinite(tokens) || tokens <= 0) return null
  if (tokens >= PER_MILLION) {
    const millions = tokens / PER_MILLION
    return t("modelPricing.cf0d24d173", { value1: millions >= 10 ? Math.round(millions) : Number(millions.toFixed(1)) })
  }
  if (tokens >= 1000) return t("modelPricing.c046494e68", { value1: Math.round(tokens / 1000) })
  return String(Math.round(tokens))
}

/**
 * The one-line meta cell a picker row shows beside the model id.
 *
 * Context precedes price because it is the shorter, more stable field and so
 * gives the eye a fixed left edge to scan the prices against.
 */
export function modelMetaLabel(model: ModelPricingFacts): string | null {
  const parts = [formatContextLength(model.context_length), formatModelPricing(model)]
    .filter((part): part is string => part !== null)
  return parts.length ? parts.join(' · ') : null
}

/**
 * What prompt caching costs on this model, stated before it is switched to.
 *
 * Read from the catalog's own pricing rather than from a list of providers,
 * because the list goes stale the week a new one appears and the pricing does
 * not. Three answers, and they are genuinely different:
 *
 *   - no read price - this model does not cache. A 0% hit rate on it is correct
 *     rather than a fault, which is the reading a spend page cannot give you.
 *   - a write price above the input price - a write is billed at a premium
 *     (1.25x on OpenAI and Anthropic), so a prefix that is written and never
 *     read back costs *more* than not caching at all.
 *   - no write price beside a read price - writes are free (DeepSeek, Z.AI,
 *     Moonshot, xAI), so caching is upside with no downside.
 *
 * `null` when the catalog said nothing either way, which is not "does not
 * cache": a bare OpenAI-compatible `/models` carries no pricing at all.
 */
export function modelCachingLabel(model: ModelPricingFacts): string | null {
  const { cache_read_price: read, cache_write_price: write, prompt_price: input } = model
  if (read === null || read === undefined || !Number.isFinite(read)) {
    // Only a model with priced input can be *said* to not cache; without any
    // pricing the catalog simply did not answer.
    return input === null || input === undefined ? null : t("modelPricing.1f718221e3")
  }
  const ratio = input && input > 0 ? read / input : null
  const readPart = ratio ? t("modelPricing.dd2e1204a4", { value1: ratio.toFixed(2) }) : t("modelPricing.c31eb28848")
  if (write !== null && write !== undefined && Number.isFinite(write) && input && write > input) {
    return t("modelPricing.51e574f950", { value1: readPart, value2: (write / input).toFixed(2) })
  }
  return t("modelPricing.06057ed9e0", { value1: readPart })
}

/**
 * The same facts spelled out for a tooltip, because the row itself cannot afford
 * to say which figure is input and which is output. A picker row is the first
 * place most readers meet `$0.08 / $0.30`, and the order is not guessable.
 */
export function modelMetaTitle(model: ModelPricingFacts): string | null {
  const lines: string[] = []
  const input = perMillionTokens(model.prompt_price)
  const output = perMillionTokens(model.completion_price)
  if (input !== null) lines.push(t("modelPricing.333debc1ab", { value1: input }))
  if (output !== null) lines.push(t("modelPricing.f1e5cb7204", { value1: output }))
  const caching = modelCachingLabel(model)
  if (caching !== null) lines.push(caching)
  const context = formatContextLength(model.context_length)
  if (context !== null) lines.push(`${context} context`)
  return lines.length ? lines.join(' · ') : null
}
