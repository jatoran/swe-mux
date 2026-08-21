/**
 * Which language-model endpoint this install talks to, and whether it is proven.
 *
 * swe-mux's speech and hearing already run on the operator's own machine; the model was
 * the last part that had to be somebody else's server. A custom OpenAI-compatible endpoint
 * - `{base_url, api_key, model}`, which is llama.cpp, Ollama, vLLM, and LM Studio with one
 * shape - is now a choice beside OpenRouter, and OpenRouter stays the default.
 *
 * The reason this module exists rather than each surface reading the status endpoint: an
 * unverified endpoint has to be legible *wherever* it makes something inert, which is the
 * Projects registry, every grant gate over a model-backed switch, and Settings itself. A
 * paraphrase per surface would drift, so the daemon's own sentence (`readiness.reason`) is
 * fetched once, cached, and rendered verbatim.
 *
 * The cache is dropped on the two events that can change the answer - a settings save and
 * a verification - rather than expiring on a timer, for the same reason `projectAutomations`
 * does it that way: a timer is wrong in both directions, and the moment that matters most
 * is the second after the operator presses Verify.
 */

// Explicit extensions: `test/all.ts` runs under node's type stripping, which does not
// resolve an extensionless relative TypeScript specifier, and this module is reached
// from a test.
import { api } from './api.ts'
import type { LlmReadiness } from './projectAutomations.ts'

export type { LlmReadiness }

export const LLM_PROVIDER_CHANGED = 'mux:llm-provider-changed'

/** One configured provider, whether or not it is the active one. */
export type LlmProviderEntry = {
  id: string
  label: string
  active: boolean
  origin: string
  model: string
  /** Whether a durable verified record gates what depends on this provider. */
  requires_verification: boolean
  /** `by_model` answers caching from the model id; `unknown` cannot answer it at all. */
  cache_policy: 'by_model' | 'unknown'
  secret: { configured: boolean; source: string; persistent?: boolean; encrypted?: boolean }
  verification: {
    provider: string
    verified: boolean
    /** A record exists but no longer matches the endpoint - edited since it was proven. */
    stale: boolean
    verified_at: number | null
    base_url: string
    model: string
    resolved_model: string
    sample: string
    latency_ms: number
  }
  readiness: LlmReadiness
}

export type ProviderStatusPayload = {
  secret: LlmProviderEntry['secret']
  models: { models: { id: string; name: string }[]; fetched_at?: number | null
    error?: string | null; stale?: boolean }
  origin: string
  cheap_model: string
  standard_model: string
  provider: string
  providers: LlmProviderEntry[]
  llm: LlmReadiness
}

export type VerifyResult = {
  ok: boolean
  provider: string
  output?: string
  requested_model?: string
  resolved_model?: string
  latency_ms?: number
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number | null
  error?: string
  verification: LlmProviderEntry['verification']
  llm: LlmReadiness
}

let pending: Promise<ProviderStatusPayload> | null = null

export function forgetLlmProvider(): void {
  pending = null
}

export function fetchLlmProvider(): Promise<ProviderStatusPayload> {
  if (pending) return pending
  const request = api<ProviderStatusPayload>('GET', '/api/automation/provider')
    // A failed read is not evidence of anything, so it is not remembered as one.
    .catch(error => { pending = null; throw error })
  pending = request
  return request
}

/**
 * Prove one endpoint with a single completion and record the result durably.
 *
 * Always resolves rather than throwing on a refusal: a `422` here carries the endpoint's
 * own explanation, which is the most useful thing on the screen, and an exception would
 * reduce it to a generic failure banner. Only a transport error rejects.
 */
export async function verifyLlmProvider(provider?: string): Promise<VerifyResult> {
  try {
    const result = await api<VerifyResult>('POST', '/api/automation/provider/verify',
      provider ? { provider } : {})
    forgetLlmProvider()
    window.dispatchEvent(new CustomEvent(LLM_PROVIDER_CHANGED, { detail: result }))
    return result
  } catch (cause) {
    forgetLlmProvider()
    throw cause
  }
}

/**
 * The heading a gated surface shows when a model-backed switch is on and inert.
 *
 * Deliberately not a restatement of `reason`: the daemon says what is wrong with the
 * endpoint, and this says what that means for the thing the reader is looking at. Both
 * are rendered, in that order.
 */
export function llmGateHeading(readiness: LlmReadiness): string {
  switch (readiness.code) {
    case 'no_key': return 'No model provider is configured, so this cannot run.'
    case 'no_endpoint':
    case 'no_model': return 'The custom model endpoint is incomplete, so this cannot run.'
    case 'endpoint_changed':
      return 'The model endpoint changed since it was verified, so this is on hold.'
    case 'unverified':
      return 'The model endpoint is not verified yet, so this is on hold.'
    default: return 'No verified model provider, so this cannot run.'
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener(LLM_PROVIDER_CHANGED, () => { pending = null })
}
