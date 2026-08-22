// The Usage dialog's segments, named where both the dialog and its own views can see them.
//
// A tile in the Overview navigates to a sibling segment, so the Overview needs the segment
// names and the dialog needs the Overview. Holding the type in the dialog would make that a
// cycle; holding it here makes the dialog's shell and its contents both consumers of one
// list, which is also what stops the segment rail and the deep-link commands from drifting.

export type UsageSegment = 'overview' | 'agents' | 'automation' | 'quota'

export type UsageSegmentDescriptor = {
  id: UsageSegment
  label: string
  /** The segmented control's tooltip, and the dialog subtitle when that segment is open. */
  title: string
  heading: string
  /**
   * The standing caveat for this segment, drawn in the dialog footer.
   *
   * It is per-segment rather than one line for the dialog because the four make different
   * promises and the sharpest of them is only true of one: ccusage reconstructs totals from
   * transcript roots that carry no trustworthy saved-account identity, so a historical row
   * must never be read as belonging to an account slot — while the quota charts beside it
   * are keyed on a *verified* provider account uuid and are exactly that claim. One footer
   * covering both would have to be vague enough to be true of neither.
   */
  footer: string
}

const NEVER_SUMMED = 'The three pots are never summed'

export const USAGE_SEGMENTS: UsageSegmentDescriptor[] = [
  {
    id: 'overview',
    label: 'Overview',
    title: 'The three pots side by side, never summed',
    heading: 'USAGE::OVERVIEW',
    footer: `${NEVER_SUMMED} · each figure carries the basis that makes it mean something`,
  },
  {
    id: 'agents',
    label: 'Agents',
    title: 'Historical model spend and tokens, read back out of transcripts by ccusage',
    heading: 'USAGE::AGENTS',
    footer: `${NEVER_SUMMED} · estimated from transcripts · historical model data is not account-specific`,
  },
  {
    id: 'automation',
    label: 'Automation',
    title: 'Metered observer calls, billed by the call and ranked by what asked for them',
    heading: 'USAGE::AUTOMATION',
    footer: `${NEVER_SUMMED} · metered per call · a window holding unpriced calls reads as a floor`,
  },
  {
    id: 'quota',
    label: 'Quota',
    title: 'Provider subscription windows, resets, and correlated activity',
    heading: 'USAGE::QUOTA',
    footer: `${NEVER_SUMMED} · quota utilization, not tokens · durable local evidence, bounded retention`,
  },
]
