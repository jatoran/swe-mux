// The quest log: three multi-step setups worth pointing at, and nothing else.
//
// The cap is the feature. A quest log that grows into a general todo list is an
// obligation handed to a user on first launch, which is worse than not having
// one - so the registry is a closed three-entry tuple (mirrored by `QUEST_IDS`
// in `src/swe_mux/config.py`), a fourth entry is a deliberate change to both,
// and nothing anywhere generates entries.
//
// Completion is honest rather than tracked: voice derives from the config keys
// the guided setup writes, and the other two - which have no single "done"
// signal a browser can read - complete only by explicit dismissal. Dismissal is
// machine-side (`quests_dismissed`) and permanent; nothing ever resurrects a
// dismissed quest, on this device or another.

export type QuestId = 'voice' | 'worktrees' | 'phone'

export type Quest = {
  id: QuestId
  title: string
  blurb: string
  /** The label on the quest's one action, which opens an existing surface. */
  action: string
}

export const QUESTS: readonly Quest[] = [
  {
    id: 'voice',
    title: 'Set up voice',
    blurb: 'Read replies aloud and talk to your agents. A guided walk covers the engine, the download, and the microphone.',
    action: 'Guided setup',
  },
  {
    id: 'worktrees',
    title: 'Try an isolated worktree',
    blurb: 'Run an agent on a branch in its own checkout - parallel work that cannot touch your main tree. Start one from Run → Isolated checkout.',
    action: 'Open Git',
  },
  {
    id: 'phone',
    title: 'Connect your phone',
    blurb: 'The whole fleet from your pocket: watch sessions, answer prompts, and talk hands-free over your tailnet.',
    action: 'Open Remote',
  },
]

export type QuestSignals = {
  tts_enabled?: boolean
  stt_enabled?: boolean
  quests_dismissed?: readonly string[]
}

/** The quests still worth drawing, in registry order. Empty hides the log forever. */
export function openQuests(signals: QuestSignals): Quest[] {
  const dismissed = new Set(signals.quests_dismissed || [])
  return QUESTS.filter(quest => {
    if (dismissed.has(quest.id)) return false
    if (quest.id === 'voice') return !signals.tts_enabled && !signals.stt_enabled
    return true
  })
}

export function withQuestDismissed(
  dismissed: readonly string[] | undefined,
  id: QuestId,
): string[] {
  const next = new Set(dismissed || [])
  next.add(id)
  // Stored in registry order so the config value does not churn with click order.
  return QUESTS.map(quest => quest.id).filter(quest => next.has(quest))
}
