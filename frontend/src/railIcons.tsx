// The app's icon-only controls: the command rail and the utility drawer's tabs.
//
// On the command rail, only the actions whose meaning survives without a word are drawn: copy,
// paste, and branch. Copy resume deliberately keeps its text label — "copy" alone cannot
// distinguish it from Copy reply, and the two sit next to each other on the rail.
//
// The drawer's tabs are all icons, and one set serves both places they appear (the strip
// inside the drawer and the always-visible desktop rail), so the two agree by construction
// rather than by two lists being kept in sync.
//
// Stroke-based and sized in CSS, not `1em`: these surfaces run a 9–10px font, which would
// render an `em`-sized icon unreadably small. That is also why they are SVG rather than the
// text glyphs they replaced — a monospace font gives every glyph one advance width but wildly
// different ink, so `!` came out a hairline next to a heavy `⧉` with no way to normalize it.

import type { VNode } from 'preact'
import type { DrawerTabId } from './drawerTabs'

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

// Drawer tabs. Each mark is chosen to be readable at 17px and to not collide with its
// neighbours: the two text-into-a-terminal tabs are a terminal and a speech bubble, and the
// two navigators are the classic folder/document pair.

/** A clipboard holding a clock: the paste surface, but the *history* of it. */
export const ClipboardHistoryIcon = () => <svg {...stroke}>
  <rect x="9" y="2" width="6" height="4" rx="1" />
  <path d="M15 4h2a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
  <circle cx="12" cy="13" r="4" />
  <path d="M12 11v2.2l1.5.9" />
</svg>

/** A terminal window. Replaces `⌘`, which is the macOS Command key on a Windows-first app. */
export const CommandsIcon = () => <svg {...stroke}>
  <rect x="2" y="4" width="20" height="16" rx="2" />
  <path d="m7 10 2.5 2.5L7 15" />
  <line x1="13" y1="15" x2="17" y2="15" />
</svg>

/** A message with body text: saved wording you send, not a discrete key action. */
export const PromptsIcon = () => <svg {...stroke}>
  <path d="M21 14a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z" />
  <line x1="8" y1="8" x2="17" y2="8" />
  <line x1="8" y1="12" x2="14" y2="12" />
</svg>

/** A folder. A folder *tree* is the truer picture but turns to mud at this size. */
export const FilesIcon = () => <svg {...stroke}>
  <path d="M3 7a2 2 0 0 1 2-2h3.6a2 2 0 0 1 1.7.9L11.5 8H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
</svg>

/** A written page. The Notes tab indexes documents; it no longer edits one. */
export const NotesIcon = () => <svg {...stroke}>
  <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
  <polyline points="14 3 14 8 19 8" />
  <line x1="9" y1="13" x2="15" y2="13" />
  <line x1="9" y1="17" x2="13" y2="17" />
</svg>

/** A commit line with a working tree hanging off it: a branch you are sitting in.
 *
 * Deliberately close kin to `BranchIcon` above — this tab is about branches, and the fork is
 * the one mark that says so. They never appear together: that one is a terminal action rail
 * button, this one a drawer tab. The extra node on the fork is what distinguishes them at
 * 16px, and it is the right difference to draw: the rail forks a conversation, this tab
 * shows the trees a fork already produced. */
export const GitIcon = () => <svg {...stroke}>
  <circle cx="6" cy="4" r="2" />
  <circle cx="6" cy="20" r="2" />
  <circle cx="18" cy="12" r="2" />
  <line x1="6" y1="6" x2="6" y2="18" />
  <path d="M16 12H12a6 6 0 0 1-6-6" />
</svg>

/** A bell. The one concept here with a mark everyone already knows. */
export const AlertsIcon = () => <svg {...stroke}>
  <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
  <path d="M13.7 21a2 2 0 0 1-3.4 0" />
</svg>

/** Every drawer tab must appear here; the strip and the rail both read this map. */
export const DRAWER_TAB_ICONS: Record<DrawerTabId, () => VNode> = {
  clipboard: ClipboardHistoryIcon,
  commands: CommandsIcon,
  prompts: PromptsIcon,
  files: FilesIcon,
  notes: NotesIcon,
  git: GitIcon,
  notifications: AlertsIcon,
}
