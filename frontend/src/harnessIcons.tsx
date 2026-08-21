// One mark per harness, for every surface that needs to say *which CLI* something is:
// the sidebar row's provider prefix, the session tab strip, the account switcher, and the
// Project Run menu's launch rows.
//
// Single source on purpose. The marks used to live beside the account switcher, which only
// ever knew the two harnesses that have provider accounts (claude, codex); everything else
// fell back to the first letter of the display name. That fallback is not merely plain, it
// is wrong: `oh-my-pi` and `opencode` both render as `O`, so the one surface whose job is to
// tell two panes apart drew them identically.
//
// Every mark is a 24-box `currentColor` drawing carrying `class="provider-mark"`, so a host
// surface sizes and colours it in CSS the way it already sized the two originals. A harness
// with no mark here still falls back to its initial - a registry the daemon can extend must
// not render nothing when it does.

import type { VNode } from 'preact'
import { harnessDisplayName } from './harnessRegistry'

const stroke = {
  class: 'provider-mark',
  viewBox: '0 0 24 24',
  width: '1em',
  height: '1em',
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': '2',
  'stroke-linecap': 'round' as const,
  'stroke-linejoin': 'round' as const,
  'aria-hidden': true,
}

/** Anthropic's burst. Heavier stroke than the rest of the set: it is all thin radials, and
 *  at the 10px a tab strip renders it the default weight disappears. */
const claudeMark = <svg {...stroke} stroke-width="2.5"><path d="M12 2v20M2 12h20M4.9 4.9l14.2 14.2M19.1 4.9 4.9 19.1"/></svg>

/** OpenAI's knot, as published - a fill path rather than a stroke, which is why it opts out
 *  of the shared attributes. */
const openaiMark = <svg class="provider-mark" viewBox="0 0 24 24" width="1em" height="1em" fill="currentColor" aria-hidden="true"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5962 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997z"/></svg>

/** A bare π: the letter the CLI is named for, and the whole mark, because it has to stay
 *  legible next to the circled version oh-my-pi wears. Three strokes and no more - a foot
 *  serif on the right leg is invisible at 10px and only costs contrast against `omp`. */
const piMark = <svg {...stroke}><path d="M4 7.5h16M9 7.5v10M16 7.5v10"/></svg>

/** oh-my-pi: the same π, ringed. The two harnesses are deliberately one mark apart, because
 *  they are one distribution apart; the ring is the "oh-my" wrapper around the CLI. Reading
 *  them as a pair at a glance matters more than either being independently evocative, and the
 *  ring survives 10px where any inner ornament would not. */
const ompMark = <svg {...stroke}><circle cx="12" cy="12" r="9.2"/><path d="M7.3 9.4h9.4M9.9 9.4v6.3M14.1 9.4v6.3" stroke-width="1.7"/></svg>

/** opencode: angle brackets around a slash - the universal "source" mark. Deliberately not a
 *  terminal window, which is the Actions tab's glyph, and deliberately not a letter, which is
 *  the collision this whole set exists to remove. */
const opencodeMark = <svg {...stroke}><polyline points="8 7.5 3.2 12 8 16.5"/><polyline points="16 7.5 20.8 12 16 16.5"/><line x1="13.6" y1="5.6" x2="10.4" y2="18.4"/></svg>

const MARKS: Record<string, VNode> = {
  claude: claudeMark,
  codex: openaiMark,
  pi: piMark,
  omp: ompMark,
  opencode: opencodeMark,
}

/**
 * The mark for a harness, or its initial when the registry names one this build has no
 * drawing for.
 *
 * The fallback is a string rather than a placeholder shape on purpose: an unknown harness is
 * a daemon newer than this frontend, and a letter from its own display name is the most a
 * browser can honestly say about it. Callers render the result inside their own element, so
 * both branches drop into the same slot.
 */
export const harnessMark = (name: string): VNode | string =>
  MARKS[name] ?? harnessDisplayName(name).slice(0, 1).toUpperCase()

/** Whether this harness has a drawn mark, for a caller that would rather draw nothing than
 *  draw a letter. */
export const hasHarnessMark = (name: string): boolean => name in MARKS
