import { rich, t } from './localization/index.ts'
import { openQuests, type Quest, type QuestId, type QuestSignals } from './questRegistry.ts'

/**
 * The quest-log card, drawn only inside the empty workspace stage - the largest
 * element on a new user's screen and, until this, an inert one (usability audit
 * finding 8). Each row is one action into an existing surface plus a permanent
 * dismissal; the host owns what the actions open and how a dismissal persists,
 * so this stays a pure projection of `openQuests`.
 */
export function QuestLog(
  { signals, onAction, onDismiss }:
  { signals: QuestSignals; onAction: (id: QuestId) => void; onDismiss: (id: QuestId) => void },
) {
  const quests = openQuests(signals)
  if (!quests.length) return null
  return <div class="quest-log" aria-label={t("QuestLog.ba8770cfc0")}>
    <h2>{rich("QuestLog.96c6973f6f", { value1: <small>{t("QuestLog.0413d8a4c3")}</small> })}</h2>
    {quests.map((quest: Quest) => <div class="quest-log-row" key={quest.id}>
      <div class="quest-log-text"><strong>{quest.title}</strong><small>{quest.blurb}</small></div>
      <button type="button" class="primary" onClick={() => onAction(quest.id)}>{quest.action}</button>
      <button type="button" title={t("QuestLog.0d41d4fa60")} aria-label={t("QuestLog.d4d093af8c", { title: quest.title })} onClick={() => onDismiss(quest.id)}>✕</button>
    </div>)}
  </div>
}
