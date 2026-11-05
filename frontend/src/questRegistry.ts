import { t } from './localization/index.ts'
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
    get title() { return t("questRegistry.35caa7c79c") },
    get blurb() { return t("questRegistry.0ca0400577") },
    get action() { return t("questRegistry.8ef7823ca8") },
  },
  {
    id: 'worktrees',
    get title() { return t("questRegistry.ea6b05aeec") },
    get blurb() { return t("questRegistry.1bd34d8c81") },
    get action() { return t("questRegistry.339ccf5fcc") },
  },
  {
    id: 'phone',
    get title() { return t("questRegistry.1d3326aa87") },
    get blurb() { return t("questRegistry.deed22ff69") },
    get action() { return t("questRegistry.c8363b9bc0") },
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
