// Glyphs for the command rail's icon-only buttons.
//
// Only the actions whose meaning survives without a word are drawn: copy, paste, and branch.
// Copy resume deliberately keeps its text label — "copy" alone cannot distinguish it from
// Copy reply, and the two sit next to each other on the rail.
//
// Stroke-based and sized in CSS (`.rail-icon svg`), not `1em`: the rail's own font is 9px,
// which would render these unreadably small.

const stroke = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': '2',
  'stroke-linecap': 'round' as const,
  'stroke-linejoin': 'round' as const,
  'aria-hidden': true,
}

/** Two offset sheets — the near-universal copy mark. */
export const CopyIcon = () => <svg {...stroke}>
  <rect x="8" y="8" width="14" height="14" rx="2" />
  <path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2" />
</svg>

/** A clipboard, which is what every toolbar in the world uses for paste. */
export const PasteIcon = () => <svg {...stroke}>
  <rect x="8" y="2" width="8" height="4" rx="1" />
  <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
</svg>

/** The git branch mark: a trunk, a fork, and the commit each ends at. */
export const BranchIcon = () => <svg {...stroke}>
  <line x1="6" y1="3" x2="6" y2="15" />
  <circle cx="18" cy="6" r="3" />
  <circle cx="6" cy="18" r="3" />
  <path d="M18 9a9 9 0 0 1-9 9" />
</svg>
