/**
 * The voice dock's presentation state — how much of the voice surface is on screen.
 *
 * This is deliberately a third axis, independent of the two it used to be tangled with:
 *
 * - **The microphone** (`conversation.phase`) is capture. Collapsing the dock must never
 *   stop it, and the dock control is not the mic control. Before this split, the only way
 *   to clear the panel off the workspace while Talk was running was `stop mic`, because
 *   the surface rendered only while capture ran.
 * - **The addressee** (`VoicePanelMode`) is who plain speech reaches — the dictation draft
 *   or the assistant. It decides which body the dock shows, and nothing about visibility.
 *
 * Three states rather than open/closed, because "out of the way" and "gone" are different
 * requests: `peek` keeps the status line and any open confirmation card reachable in one
 * thin row, and `chip` gives the workspace back entirely while the dialog, its speech, and
 * its earcons keep running from the same mounted component.
 */
export type VoiceDockState = 'chip' | 'peek' | 'full'

/**
 * Which body the dock draws, and — for the two conversational ones — who plain speech
 * reaches. It is not a visibility axis: changing it never opens or closes the dock, and
 * collapsing the dock never changes it — a chat-addressed microphone keeps feeding the
 * assistant from the chip.
 *
 * `read` is the third body and the operational home of read aloud: the master switch, the
 * focused session's participation and content mode, this device's autoplay, and the global
 * clip list. It is a control panel rather than a correspondent, which is what
 * `voiceAddressee` below turns on.
 */
export type VoicePanelMode = 'dictation' | 'chat' | 'read'

export const isVoicePanelMode = (value: unknown): value is VoicePanelMode =>
  value === 'dictation' || value === 'chat' || value === 'read'

/**
 * With capture off there is no draft to dictate into, so the dock shows the assistant
 * whatever the stored mode says. The stored value is left alone: turning the microphone
 * back on returns to the tab the operator chose. `read` is unaffected either way - it
 * needs no microphone and says nothing about one.
 */
export const effectiveVoicePanelMode = (mode: VoicePanelMode, talkActive: boolean): VoicePanelMode =>
  mode === 'dictation' && !talkActive ? 'chat' : mode

/**
 * Who plain speech reaches while capture runs.
 *
 * Exactly one body is a correspondent for dictation: the draft has no other surface, so
 * speech may only land there while the draft is the body on screen. Every other body -
 * the assistant, and the read-aloud panel, which is a control surface with no
 * conversation behind it - leaves the assistant as the addressee. That is a strict
 * generalization of the shipped rule (chat means the assistant), not a new one, and the
 * dock states it in the header rather than redirecting speech silently.
 */
export const voiceAddressee = (
  mode: VoicePanelMode,
  talkActive: boolean,
): 'dictation' | 'assistant' =>
  effectiveVoicePanelMode(mode, talkActive) === 'dictation' ? 'dictation' : 'assistant'

/** How much of a dock body a given dock state draws. */
export type VoiceBodyVariant = 'full' | 'peek' | 'hidden'

/**
 * A body is drawn only when the dock is open *and* that body is the selected one. Every
 * body stays mounted either way — `hidden` is a rendering, not an unmount — because the
 * assistant's event listener, speech streams, and announced-card set live inside it.
 */
export const voiceBodyVariant = (
  dock: VoiceDockState,
  mode: VoicePanelMode,
  body: VoicePanelMode,
): VoiceBodyVariant => (dock === 'chip' || mode !== body ? 'hidden' : dock)

/** The two states the dock can be restored *to* from the chip. */
export type VoiceDockExpanded = Exclude<VoiceDockState, 'chip'>

export type VoiceDockModel = {
  state: VoiceDockState
  /**
   * The expanded state the chip reopens into, so collapsing to the top bar and coming
   * back does not silently promote a deliberate `peek` to a full panel.
   */
  expanded: VoiceDockExpanded
  /**
   * True while capture — not the operator — is what opened the dock, which is the only
   * case where stopping the microphone may close it again. An explicit dock action always
   * clears the loan, so a dock the operator adjusted is theirs from then on.
   */
  borrowed: boolean
}

export type VoiceDockEvent =
  /** Operator picked a state outright (chip button, palette command). */
  | { kind: 'set'; state: VoiceDockState }
  /** Operator stepped one notch (the header's two carets). */
  | { kind: 'expand' }
  | { kind: 'collapse' }
  /** Operator toggled between the chip and whatever was last expanded. */
  | { kind: 'toggle' }
  /** Capture started or stopped; `addressee` is who plain speech is reaching. */
  | { kind: 'capture'; active: boolean; addressee: 'dictation' | 'assistant' }
  /**
   * Something appeared that must not be invisible — today, an open confirmation card,
   * which carries a countdown and, for a consequential action, a decision only a human
   * can make. Raises the dock to at least `state` and never lowers it.
   */
  | { kind: 'floor'; state: VoiceDockExpanded }

const ORDER: VoiceDockState[] = ['chip', 'peek', 'full']
const RANK: Record<VoiceDockState, number> = { chip: 0, peek: 1, full: 2 }

export const DEFAULT_VOICE_DOCK: VoiceDockModel = { state: 'chip', expanded: 'full', borrowed: false }

export const isVoiceDockState = (value: unknown): value is VoiceDockState =>
  value === 'chip' || value === 'peek' || value === 'full'

export const isVoiceDockExpanded = (value: unknown): value is VoiceDockExpanded =>
  value === 'peek' || value === 'full'

/** An operator-chosen state: it also becomes the state the chip reopens into. */
const settle = (model: VoiceDockModel, state: VoiceDockState): VoiceDockModel => ({
  state,
  expanded: state === 'chip' ? model.expanded : state,
  borrowed: false,
})

const step = (state: VoiceDockState, offset: number): VoiceDockState =>
  ORDER[Math.min(ORDER.length - 1, Math.max(0, RANK[state] + offset))]

export function reduceVoiceDock(model: VoiceDockModel, event: VoiceDockEvent): VoiceDockModel {
  switch (event.kind) {
    case 'set':
      return settle(model, event.state)
    case 'expand':
      return settle(model, step(model.state, 1))
    case 'collapse':
      return settle(model, step(model.state, -1))
    case 'toggle':
      return settle(model, model.state === 'chip' ? model.expanded : 'chip')
    case 'floor':
      // Never a loan and never a demotion: a card that opens over a full dock leaves it
      // full, and a card that opens over the chip leaves a peek the operator now owns.
      return RANK[model.state] >= RANK[event.state] ? model : { ...model, state: event.state, borrowed: false }
    case 'capture':
      if (event.active) {
        // The dictation draft has no other surface — starting Talk with the dock at the
        // chip would leave the operator dictating into something they cannot see. The
        // assistant is deliberately excluded: it speaks its replies and badges the chip,
        // which is the whole point of being able to leave it collapsed and live.
        if (event.addressee !== 'dictation' || model.state !== 'chip') return model
        return { state: model.expanded, expanded: model.expanded, borrowed: true }
      }
      return model.borrowed ? { state: 'chip', expanded: model.expanded, borrowed: false } : model
  }
}

/** True when this model is the operator's own choice and therefore worth persisting. */
export const voiceDockPersistable = (model: VoiceDockModel): boolean => !model.borrowed

const STATE_KEY = 'mux.voice.dock'
const EXPANDED_KEY = 'mux.voice.dock.expanded'

export function loadVoiceDock(): VoiceDockModel {
  try {
    const state = localStorage.getItem(STATE_KEY)
    const expanded = localStorage.getItem(EXPANDED_KEY)
    return {
      state: isVoiceDockState(state) ? state : DEFAULT_VOICE_DOCK.state,
      expanded: isVoiceDockExpanded(expanded) ? expanded : DEFAULT_VOICE_DOCK.expanded,
      borrowed: false,
    }
  } catch {
    return { ...DEFAULT_VOICE_DOCK }
  }
}

export function saveVoiceDock(model: VoiceDockModel): void {
  if (!voiceDockPersistable(model)) return
  try {
    localStorage.setItem(STATE_KEY, model.state)
    localStorage.setItem(EXPANDED_KEY, model.expanded)
  } catch {
    /* private mode */
  }
}

/** What the header's collapse/expand carets are allowed to do from here. */
export const canExpandVoiceDock = (state: VoiceDockState): boolean => state !== 'full'
export const canCollapseVoiceDock = (state: VoiceDockState): boolean => state !== 'chip'
