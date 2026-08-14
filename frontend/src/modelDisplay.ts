// Compact model labels for read-only surfaces.
//
// Two things are removed, in this order, and nothing else is touched:
//
//   - the provider path (`anthropic/claude-opus-5`, `openai/codex`). An
//     openrouter-style id names the vendor before the model, and the vendor is
//     not the model.
//   - a leading vendor-brand token (`claude-`, `gpt-`). Every surface that
//     prints a model draws the session's provider mark beside it, so the brand
//     is the one part of the id the reader already has without reading.
//
// A token that names a model *family* is not branding and stays: `codex`,
// `kimi`, `o3`, and `sonnet` are what tell one model from another.
//
// This replaced a per-family prefix table, which had to be extended by hand for
// every new model and printed the raw id until someone did — the sidebar showed
// `opus-5` beside `claude-fable-5`, and `sonnet-4-6` beside `gpt-5.6-sol`, in
// the same list, because only some of them had been added. A rule about what a
// model id *is* cannot fall behind the models.
//
// The exact identifier is never lost: every surface here keeps it in the
// tooltip, and configuration, comparison, and API values use it untouched.

/**
 * The vendor segment of a namespaced id. Deliberately any vendor, not a known
 * list: an unfamiliar vendor is precisely the case where a reader has the least
 * room to spare, and keeping the path only for those made the display
 * inconsistent exactly where it was hardest to read.
 */
const PROVIDER_PATH = /^[a-z0-9][a-z0-9._-]*\//i

/**
 * Leading tokens that name a vendor's brand rather than a model.
 *
 * The test for membership is "would the provider mark beside this label already
 * have told me?" — `claude` and `gpt` qualify, `codex` and `kimi` do not.
 */
export const MODEL_VENDOR_TOKENS: readonly string[] = [
  'anthropic', 'claude', 'openai', 'chatgpt', 'gpt',
]

export function displayModelName(model: string): string {
  let name = model.replace(PROVIDER_PATH, '')
  // Looped rather than single-pass so a doubly-branded id (`anthropic-claude-…`)
  // reduces the same way a singly-branded one does.
  for (let pass = 0; pass < MODEL_VENDOR_TOKENS.length; pass += 1) {
    const token = MODEL_VENDOR_TOKENS.find(
      candidate => name.toLowerCase().startsWith(`${candidate}-`),
    )
    if (!token) break
    const rest = name.slice(token.length + 1)
    // A brand with nothing after it is the whole name it has: `claude-` reduced
    // to an empty label would be a row that names no model at all.
    if (!rest) break
    name = rest
  }
  return name || model
}
