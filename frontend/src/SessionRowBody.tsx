// Renders the token model produced by sessionRowFields.ts.
//
// Two structural rules live here and nowhere else:
//
//  - a separator is emitted only *between tokens that drew*, so a conditional
//    field that hid leaves no dangling ` · ` and no doubled one;
//  - the left and right sections never overlap. They are flex siblings with a
//    mandatory gap; when the sidebar narrows, the left section sheds whole
//    low-priority tokens rather than ellipsizing every token into a row of
//    prefixes, and only then does what remains truncate.

import { harnessDisplayName } from './harnessRegistry.ts'
import { agentTargetName } from './agentTargets.ts'
import { providerGlyph } from './ProviderAccounts'
import type { RowSection, RowToken, SessionRowTokens } from './sessionRowFields.ts'
import type { SessionRowConfig } from './sessionRowConfig.ts'
import type { Session } from './types'

/** Gauge cells. Four is enough to compare rows at a glance and cheap to scan. */
const GAUGE_CELLS = 4
/** Counts at or below this render as pips; above it, as a numeral. */
const MAX_PIPS = 4
/** How many of a section's lowest-priority tokens get a width-shed class. */
const SHED_TIERS = 3

function gaugeCells(pct: number, peak: number) {
  const filled = Math.max(1, Math.ceil(Math.min(1, pct) * GAUGE_CELLS))
  const peakCell = Math.max(1, Math.ceil(Math.min(1, peak) * GAUGE_CELLS))
  return Array.from({ length: GAUGE_CELLS }, (_, index) => ({
    on: index < filled,
    peak: index + 1 === peakCell && peakCell > filled,
  }))
}

/**
 * Five cells split between additions and deletions, sized by ratio.
 *
 * Real diffs span one line to ten thousand, so magnitude is logarithmic; the
 * exact counts remain available as the `numbers` rendering and in the tooltip.
 */
function diffCells(added: number, removed: number) {
  const total = added + removed
  if (total <= 0) return Array.from({ length: 5 }, () => 'off' as const)
  const addCells = Math.round((added / total) * 5)
  const magnitude = Math.min(5, Math.max(1, Math.ceil(Math.log10(total + 1) * 2)))
  return Array.from({ length: 5 }, (_, index) =>
    index >= magnitude ? 'off' as const : index < addCells ? 'add' as const : 'del' as const)
}

function TokenView({ token, session, config }: { token: RowToken; session: Session; config: SessionRowConfig }) {
  if (token.kind === 'title') {
    // Deliberately a <strong>: the sidebar's attention tiers (active / unread /
    // viewing / read) are expressed as `.session-copy strong` colour rules, and
    // those tiers are load-bearing, not decoration.
    return <strong class="row-title">{agentTargetName(session)}</strong>
  }
  if (token.kind === 'glyph') {
    return <span class={`agent-prefix ${session.backend}`} title={harnessDisplayName(session.backend)}>{providerGlyph(session.backend)}</span>
  }
  if (token.kind === 'broadcast') {
    return <span class="broadcast-flag" title={token.title}>⇶</span>
  }
  if (token.kind === 'badges') {
    return <span class="activity-badges" role="img" aria-label={token.text}>
      {(token.badges || []).map((badge, index) =>
        <span key={index} class="activity-badge" title={badge.title}>
          {badge.glyph}{badge.count && badge.count > 1 ? <span class="activity-count">{badge.count}</span> : null}
        </span>)}
    </span>
  }
  if (token.kind === 'gauge' && token.gauge) {
    const pct = token.gauge.pct
    const tone = pct >= 0.9 ? ' crit' : pct >= 0.7 ? ' hot' : ''
    return <span class={`row-gauge${tone}`} title={token.title} role="img" aria-label={token.text}>
      {gaugeCells(pct, token.gauge.peak).map((cell, index) =>
        <i key={index} class={`${cell.on ? 'on' : ''}${cell.peak ? ' peak' : ''}`} />)}
    </span>
  }
  if (token.kind === 'diff' && token.diff) {
    if (config.diffStyle === 'bar') {
      return <span class="row-diffbar" title={token.title} role="img" aria-label={token.text}>
        {diffCells(token.diff.added, token.diff.removed).map((cell, index) => <i key={index} class={cell} />)}
      </span>
    }
    return <span class="row-diff" title={token.title}>
      <span class="add">+{token.diff.added}</span> <span class="del">-{token.diff.removed}</span>
    </span>
  }
  if (token.kind === 'count' && config.countStyle === 'pips' && (token.count ?? 0) <= MAX_PIPS) {
    const count = token.count ?? 0
    if (!count) return <span class="row-text muted" title={token.title}>·</span>
    return <span class="row-pips" title={token.title} role="img" aria-label={token.text}>
      {Array.from({ length: count }, (_, index) => <i key={index} />)}
    </span>
  }
  return <span class={`row-text${token.tone && token.tone !== 'default' ? ` ${token.tone}` : ''}`} title={token.title}>{token.text}</span>
}

/**
 * Shed class per token: the lowest-priority tokens in a section are the first to
 * disappear as the sidebar narrows. The title never sheds — a row without one
 * is not identifiable.
 */
function shedClasses(section: RowSection): Map<RowToken, string> {
  const ranked = [...section.tokens]
    .filter(token => token.kind !== 'title')
    .sort((a, b) => a.priority - b.priority)
  const classes = new Map<RowToken, string>()
  ranked.slice(0, SHED_TIERS).forEach((token, index) => classes.set(token, `shed-${index + 1}`))
  return classes
}

function SectionView({ section, session, config }: { section: RowSection; session: Session; config: SessionRowConfig }) {
  if (!section.tokens.length) return <span class={`row-section ${section.align}`} />
  const shed = shedClasses(section)
  return <span class={`row-section ${section.align}`}>
    {section.tokens.map((token, index) =>
      <span key={`${token.id}:${index}`} class={`row-token${shed.get(token) ? ` ${shed.get(token)}` : ''}`}>
        {index > 0 && section.separator ? <span class="row-sep" aria-hidden="true">{section.separator}</span> : null}
        <TokenView token={token} session={session} config={config} />
      </span>)}
  </span>
}

export function SessionRowBody(
  { session, tokens, config }: { session: Session; tokens: SessionRowTokens; config: SessionRowConfig },
) {
  const hasBottom = tokens.bottom.left.tokens.length > 0 || tokens.bottom.right.tokens.length > 0
  return <span class="session-copy">
    <span class="row-line top">
      <SectionView section={tokens.top.left} session={session} config={config} />
      <SectionView section={tokens.top.right} session={session} config={config} />
    </span>
    {/* The empty bottom line is kept, not collapsed: a constant row height is
        what makes the list scannable, and the blanks read as "nothing to
        report" faster than any glyph could. */}
    <span class={`row-line bottom${hasBottom ? '' : ' empty'}`}>
      <SectionView section={tokens.bottom.left} session={session} config={config} />
      <SectionView section={tokens.bottom.right} session={session} config={config} />
    </span>
  </span>
}
