// The app's icon-only controls: the command rail and the utility drawer's tabs.
//
// On the command rail, only the actions whose meaning survives without a word are drawn: attach,
// copy, paste, and branch. Copy resume deliberately keeps its text label — "copy" alone cannot
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

/** A paperclip, the conventional file-attachment mark. */
export const AttachIcon = () => <svg {...stroke}>
  <path d="m21.4 11.1-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" />
</svg>

/** A plain right arrow: the submit mark, on the pinned mobile Send end-cap.
 *
 * Not a paper plane and not a chevron. The plane says "message" on a control that submits
 * whatever is composed (a slash command, an approval, a bare Enter), and a lone chevron is the
 * mark the rail's own key buttons wear. An arrow into the terminal is what the button does. */
export const SendIcon = () => <svg {...stroke}>
  <line x1="3" y1="12" x2="19" y2="12" />
  <polyline points="13 6 19 12 13 18" />
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

/** A stack of lines feeding a chevron: things waiting in order, and the way out.
 *
 * Not an envelope, which would say "mail" — the queue is ordered and the order is the whole
 * point of it. The chevron is what stops three lines reading as a hamburger menu. */
export const QueueIcon = () => <svg {...stroke}>
  <line x1="3" y1="6" x2="13" y2="6" />
  <line x1="3" y1="12" x2="13" y2="12" />
  <line x1="3" y1="18" x2="13" y2="18" />
  <polyline points="17 8 21 12 17 16" />
</svg>

/** Two bubbles, tails on opposite sides: an exchange, read back.
 *
 * Deliberately built from the same bubble as `PromptsIcon` and deliberately doubled, because
 * the difference between the two tabs is exactly one of number — that one holds a message you
 * are about to send, this one holds both halves of what was already said. Body lines are
 * omitted: at 17px a second bubble and three strokes inside it is mud. */
export const TranscriptIcon = () => <svg {...stroke}>
  <path d="M3 4.5A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5v4A1.5 1.5 0 0 1 13.5 10H7l-4 3z" />
  <path d="M21 14.5A1.5 1.5 0 0 0 19.5 13h-9A1.5 1.5 0 0 0 9 14.5v4A1.5 1.5 0 0 0 10.5 20H17l4 3z" />
</svg>

/** A hexagonal agent core with three capability ports. */
export const AgentIcon = () => <svg {...stroke}>
  <path d="m12 3 6 3.5v7L12 17l-6-3.5v-7z" />
  <circle cx="12" cy="10" r="2.5" />
  <path d="M12 17v4M6 13.5l-3 2M18 13.5l3 2" />
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

/** Brackets around linked memory nodes: context held around facts, not another document. */
export const ContextIcon = () => <svg {...stroke}>
  <path d="M8 3H6a2 2 0 0 0-2 2v4a2 2 0 0 1-2 2 2 2 0 0 1 2 2v6a2 2 0 0 0 2 2h2" />
  <path d="M16 3h2a2 2 0 0 1 2 2v4a2 2 0 0 0 2 2 2 2 0 0 0-2 2v6a2 2 0 0 1-2 2h-2" />
  <circle cx="12" cy="8" r="1.5" />
  <circle cx="12" cy="16" r="1.5" />
  <line x1="12" y1="9.5" x2="12" y2="14.5" />
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

/** A pulse on a screen: something running, and whether it is doing anything.
 *
 * Not a gauge or a chip — this tab is about live activity per session, and the trace is the one
 * mark that reads as "running" rather than "capacity". */
export const ProcessesIcon = () => <svg {...stroke}>
  <rect x="2" y="4" width="20" height="16" rx="2" />
  <path d="M5 13h3l2-4 2.5 7 2-3h4.5" />
</svg>

/** A bell. The one concept here with a mark everyone already knows. */
export const AlertsIcon = () => <svg {...stroke}>
  <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
  <path d="M13.7 21a2 2 0 0 1-3.4 0" />
</svg>

/** A frame with its right-hand column partitioned off: the side panel itself, rather
 *  than whichever tab happens to be showing in it. Used by the mobile toolbar's drawer
 *  toggle, which opens the drawer on its last tab and so cannot name one. */
export const SidePanelIcon = () => <svg {...stroke}>
  <rect x="2" y="4" width="20" height="16" rx="2" />
  <line x1="15" y1="4" x2="15" y2="20" />
</svg>

/** `SidePanelIcon` mirrored: the navigation sidebar is the same panel on the other edge, and
 *  the two mobile edge toggles read as one pair only if their marks are one mark reflected. */
export const NavPanelIcon = () => <svg {...stroke}>
  <rect x="2" y="4" width="20" height="16" rx="2" />
  <line x1="9" y1="4" x2="9" y2="20" />
</svg>

/** Every drawer tab must appear here; the strip and the rail both read this map. */
export const DRAWER_TAB_ICONS: Record<DrawerTabId, () => VNode> = {
  clipboard: ClipboardHistoryIcon,
  commands: CommandsIcon,
  prompts: PromptsIcon,
  queue: QueueIcon,
  transcript: TranscriptIcon,
  agent: AgentIcon,
  files: FilesIcon,
  notes: NotesIcon,
  context: ContextIcon,
  git: GitIcon,
  processes: ProcessesIcon,
  notifications: AlertsIcon,
}
