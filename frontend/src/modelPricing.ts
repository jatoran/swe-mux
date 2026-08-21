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
  if (model.prompt_price === 0 && model.completion_price === 0) return 'free'
  return `${input ?? '?'} / ${output ?? '?'} per M`
}

/** Context window as a reader states it: `128K`, `1M`. `null` when unstated. */
export function formatContextLength(tokens: number | null | undefined): string | null {
  if (tokens === null || tokens === undefined || !Number.isFinite(tokens) || tokens <= 0) return null
  if (tokens >= PER_MILLION) {
    const millions = tokens / PER_MILLION
    return `${millions >= 10 ? Math.round(millions) : Number(millions.toFixed(1))}M`
  }
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`
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
 * The same facts spelled out for a tooltip, because the row itself cannot afford
 * to say which figure is input and which is output. A picker row is the first
 * place most readers meet `$0.08 / $0.30`, and the order is not guessable.
 */
export function modelMetaTitle(model: ModelPricingFacts): string | null {
  const lines: string[] = []
  const input = perMillionTokens(model.prompt_price)
  const output = perMillionTokens(model.completion_price)
  if (input !== null) lines.push(`Input ${input} per million tokens`)
  if (output !== null) lines.push(`Output ${output} per million tokens`)
  const context = formatContextLength(model.context_length)
  if (context !== null) lines.push(`${context} context`)
  return lines.length ? lines.join(' · ') : null
}
