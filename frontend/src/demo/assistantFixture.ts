/**
 * The Mux assistant, faked: the routes its panel reads and the acts a scenario performs.
 *
 * The demo does not listen to a microphone and does not synthesise speech, and both of
 * those are deliberate rather than missing. A demo that asked for microphone permission
 * on a marketing page would be asking a stranger for a permission it cannot justify, and
 * a demo whose value depended on audio playing would be a demo that fails silently on
 * every phone that has autoplay locked down. What is actually interesting about the voice
 * assistant is not that it hears you - every product hears you now - it is what happens
 * *after* it understands: it answers out of the live fleet, and anything it wants to do
 * to that fleet becomes a card a human resolves. That part is entirely visible, so that
 * is the part this shows.
 *
 * Everything here goes through the demo store, so the conversation exists in both frames
 * (`store.ts`), and every event the panel advances on is raised by the same mutation
 * fan-out that already carries the queue and the land trail (`fakeSocket.ts`). The panel
 * is the product's own, unmodified, and it cannot tell the difference.
 */
import type { AssistantAction } from '../assistant.ts'
import { DEMO_DIALOG_ID } from './fixtures.ts'
import { apply, demoId, nowSeconds, state } from './store.ts'

/** `GET /api/assistant`. Budget and spend are real shapes with fixture numbers. */
export function assistantStatusPayload(): unknown {
  const config = state.config as Record<string, unknown>
  return {
    enabled: config.assistant_enabled === true,
    model: String(config.assistant_model || 'demo-assistant'),
    daily_budget: config.assistant_daily_budget ?? { tokens: null, usd: 2, mode: 'usd' },
    budget_status: { allowed: true, reason: '', spent_usd: 0.04, limit_usd: 2 },
    spend_today: { tokens: 4_820, cost_usd: 0.04 },
    trust_reversible: String(config.assistant_trust_reversible || 'cancel_window'),
    diagnostic: null,
  }
}

/** `GET /api/assistant/dialogs/{id}`, which is what the panel rebuilds itself from. */
export function assistantDialogPayload(): unknown {
  return {
    dialog: {
      id: state.assistant.dialogId,
      created_at: state.assistant.createdAt,
      updated_at: nowSeconds(),
      title: state.assistant.title,
    },
    messages: state.assistant.messages,
    actions: state.assistant.actions,
    turn_running: state.assistant.turnRunning,
  }
}

export const assistantDialogId = (): string => state.assistant.dialogId || DEMO_DIALOG_ID

// ------------------------------------------------------------- what a scenario does

/** The turn the scripted conversation runs in. One id, so every beat appends to it. */
export const DEMO_TURN_ID = 'turn-voice-demo'
/** And one message id for the reply, because a reply is one bubble filled sentence by
 *  sentence rather than several bubbles in a row. */
export const DEMO_REPLY_ID = 'msg-voice-demo'

/** What the operator said, accepted by the daemon. */
export function assistantHeard(text: string): void {
  apply({ kind: 'assistant-turn', turnId: DEMO_TURN_ID, text })
}

/** One sentence of the reply. */
export function assistantSays(display: string): void {
  apply({
    kind: 'assistant-say',
    turnId: DEMO_TURN_ID,
    messageId: DEMO_REPLY_ID,
    display,
  })
}

/**
 * A confirmation card, in its cancel window.
 *
 * `reversible` with a window rather than `read` or `consequential` on purpose: it is the
 * class that shows the actual design. A read needs no card, a consequential act always
 * blocks, and the interesting middle is the one where the assistant says what it is about
 * to do, starts a clock, and does it unless a person says otherwise.
 */
export function assistantProposes(input: {
  kind: string
  restatement: string
  arguments: Record<string, unknown>
  windowSeconds?: number
}): AssistantAction {
  const action: AssistantAction = {
    id: demoId('act'),
    dialog_id: assistantDialogId(),
    turn_id: DEMO_TURN_ID,
    created_at: nowSeconds(),
    kind: input.kind,
    action_class: 'reversible',
    restatement: input.restatement,
    arguments: input.arguments,
    status: 'scheduled',
    expires_at: nowSeconds() + (input.windowSeconds ?? 12),
  }
  apply({ kind: 'assistant-action', action })
  return action
}

/** The card closing, and the line that stays behind it. */
export function assistantResolved(actionId: string, display: string): void {
  apply({ kind: 'assistant-resolved', actionId, display, status: 'executed' })
}

export function assistantDone(): void {
  apply({ kind: 'assistant-done', turnId: DEMO_TURN_ID, messageId: DEMO_REPLY_ID })
}

/** The newest open card, for a beat that resolves whatever the previous one proposed. */
export const openAssistantAction = (): AssistantAction | undefined =>
  [...state.assistant.actions].reverse().find(item => item.status === 'scheduled')
