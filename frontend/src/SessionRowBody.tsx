// Renders the token model produced by sessionRowFields.ts.
//
// Three structural rules live here and nowhere else:
//
//  - a separator is emitted only *between tokens that drew*, so a conditional
//    field that hid leaves no dangling ` · ` and no doubled one;
//  - the left and right sections never overlap, and on both lines the RIGHT one
//    is laid out first: it is what the reader deliberately pinned to the row's
//    edge, so it must not be pushed off that edge by a long value on the left.
//    The left section takes what remains and ellipsizes into it;
//  - a token drawn at the `icon` rung renders its field's mark and nothing else.
//    The value is not lost — it is in the tooltip every token already carries —
//    and the mark is drawn whatever `gitGlyphs` says, because at this width it is
//    the field's identity rather than decoration.

import { harnessDisplayName } from './harnessRegistry.ts'
import { agentTargetName } from './agentTargets.ts'
import { providerGlyph } from './ProviderAccounts'
import type { RowSection, RowToken, SessionRowTokens } from './sessionRowFields.ts'
import { GAUGE_CELLS, MAX_PIPS, type SessionRowConfig } from './sessionRowConfig.ts'
import type { Session } from './types'

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

/**
 * The scope mark a token carries, drawn ahead of its value.
 *
 * Inside the same element as the value so the shared-checkout underline covers
 * both: a mark that sat outside would read as belonging to the separator.
 */
function Prefix({ token }: { token: RowToken }) {
  if (!token.prefix) return null
  return <span class="row-prefix" aria-hidden="true">{token.prefix}</span>
}

function TokenView({ token, session, config }: { token: RowToken; session: Session; config: SessionRowConfig }) {
  if (token.display === 'icon' && token.glyph) {
    // Ahead of every kind-specific branch, because the rung is a statement about
    // the whole token: at this width the field is present and identified, and its
    // value is in the tooltip. `role="img"` with the value as the label keeps that
    // true for a screen reader, where no width pressure exists.
    return <span class="row-icon" title={token.title || token.text} role="img" aria-label={token.title || token.text}>{token.glyph}</span>
  }
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
  if (token.kind === 'draft') {
    // A caret bar, because the fact is "a cursor is sitting in text here". It is
    // the one mark in the strip that reports something *you* left behind rather
    // than something the agent is doing, so it takes the input accent rather
    // than the muted grey the standing badges share.
    return <span class="row-draft" title={token.title} role="img" aria-label={token.text}>▌</span>
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
        <Prefix token={token} />
        {diffCells(token.diff.added, token.diff.removed).map((cell, index) => <i key={index} class={cell} />)}
      </span>
    }
    return <span class="row-diff" title={token.title}>
      <Prefix token={token} />
      <span class="add">+{token.diff.added}</span> <span class="del">-{token.diff.removed}</span>
    </span>
  }
  if (token.kind === 'count' && config.countStyle === 'pips' && (token.count ?? 0) <= MAX_PIPS) {
    const count = token.count ?? 0
    if (!count) return <span class="row-text muted" title={token.title}><Prefix token={token} />·</span>
    return <span class="row-pips" title={token.title} role="img" aria-label={token.text}>
      <Prefix token={token} />
      {Array.from({ length: count }, (_, index) => <i key={index} />)}
    </span>
  }
  return <span class={`row-text${token.tone && token.tone !== 'default' ? ` ${token.tone}` : ''}`} title={token.title}>
    <Prefix token={token} />{token.text}
  </span>
}

function SectionView({ section, session, config }: { section: RowSection; session: Session; config: SessionRowConfig }) {
  if (!section.tokens.length) return <span class={`row-section ${section.align}`} />
  return <span class={`row-section ${section.align}`}>
    {/* The separator is emitted between tokens that are actually in this list —
        which is why width shedding happens in the token engine and never as a
        CSS `display:none`, whose hidden token would leave its separator here. */}
    {section.tokens.map((token, index) =>
      <span key={`${token.id}:${index}`} class={`row-token${token.shared ? ' shared' : ''}`}>
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
