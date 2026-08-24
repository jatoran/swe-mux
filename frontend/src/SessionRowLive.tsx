// The sidebar row's body, with the ageing clock inside it rather than above it.
//
// A sidebar row is the only thing in the app that has to re-read the wall clock on
// its own: "12m" is not a fact about the session, it is a fact about now. Deriving
// that at the composition root made the five-second tick a state change on the
// shell, so every menu, drawer, pane frame and tab strip re-rendered to age a
// handful of rows (terminal panes were spared only by TerminalPane's own memo
// comparator). The tick lands here instead, on the leaves that actually consume it.
//
// `useRowClock` shares one interval across every subscriber, so the sidebar still
// runs one timer however many rows are on screen.

import { useMemo } from 'preact/hooks'
import { memo } from 'preact/compat'
import { SessionRowBody } from './SessionRowBody'
import { buildSessionRowTokens, identityRowTokens, type SessionRowFleetFacts } from './sessionRowFields.ts'
import { useRowClock } from './sessionRowPrefs.ts'
import type { SessionRowConfig } from './sessionRowConfig.ts'
import type { Session } from './types'

export type SessionRowLiveProps = {
  session: Session
  config: SessionRowConfig
  /** The clock-free half of the row context, derived once per fleet snapshot. */
  facts: SessionRowFleetFacts
  /**
   * The phone's narrow projection: identity only, plus the flag strip. Rows are
   * narrower there than most tokens are useful in, and a row that truncates its own
   * title to make room for a branch name has traded down.
   */
  identityOnly: boolean
}

function SessionRowLiveImpl({ session, config, facts, identityOnly }: SessionRowLiveProps) {
  const now = useRowClock()
  const tokens = useMemo(
    () => {
      const context = { ...facts, now }
      return identityOnly ? identityRowTokens(session, config, context) : buildSessionRowTokens(session, config, context)
    },
    [session, config, facts, identityOnly, now],
  )
  return <SessionRowBody session={session} tokens={tokens} config={config} />
}

/**
 * Memoized on its four props, all of which are values the shell already keeps
 * stable across renders: the session record (replaced only by a fleet snapshot),
 * the row configuration, and the derived fleet facts. Without this the shell's own
 * re-renders would rebuild every row's tokens, which is the cost this file exists
 * to remove.
 */
export const SessionRowLive = memo(SessionRowLiveImpl, (previous, next) =>
  previous.session === next.session
  && previous.config === next.config
  && previous.facts === next.facts
  && previous.identityOnly === next.identityOnly)
