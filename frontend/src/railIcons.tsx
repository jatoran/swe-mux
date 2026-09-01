// The app's icon-only controls: the Action rail and the utility drawer's tabs.
//
// On the Action rail, only the actions whose meaning survives without a word are drawn: attach,
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

/** A text cursor: select *part* of this, rather than all of it.
 *
 * The I-beam is the mark for the gesture the button starts, and it is the one shape that
 * cannot be confused with `CopyIcon` beside it in the transcript's chip row - a single
 * upright beam against two offset sheets. Drawn bare rather than over rules of text: this
 * renders at 12px, where a beam crossing two more strokes is a smudge, and the silhouette
 * is what has to survive. */
export const SelectTextIcon = () => <svg {...stroke}>
  <path d="M8 4h8M8 20h8M12 4v16" />
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

/** A shallow drawer cabinet: the Action rail's permanent route to the complete row. */
export const RailDrawerIcon = () => <svg {...stroke}>
  <rect x="5" y="4" width="14" height="16" />
  <line x1="5" y1="10" x2="19" y2="10" />
  <line x1="5" y1="15" x2="19" y2="15" />
  <line x1="10.5" y1="7" x2="13.5" y2="7" />
  <line x1="10.5" y1="12.5" x2="13.5" y2="12.5" />
  <line x1="10.5" y1="17.5" x2="13.5" y2="17.5" />
</svg>

/** Compact sliders, used for Configure Actions inside the row popover. */
export const RailSettingsIcon = () => <svg {...stroke}>
  <line x1="4" y1="7" x2="20" y2="7" />
  <circle cx="9" cy="7" r="2" fill="var(--panel2)" />
  <line x1="4" y1="17" x2="20" y2="17" />
  <circle cx="15" cy="17" r="2" fill="var(--panel2)" />
</svg>

/** The built-ins whose meaning has a rail-sized mark. The catalog and live rail both read
 * this registry so an icon action never turns back into a text-only chip while configuring. */
const RAIL_ITEM_ICON_COMPONENTS: Readonly<Record<string, () => VNode>> = {
  attach: AttachIcon,
  branch: BranchIcon,
  copyReply: CopyIcon,
  paste: PasteIcon,
}

export function railItemHasIcon(id: string): boolean {
  return !!RAIL_ITEM_ICON_COMPONENTS[id]
}

export function RailItemIcon({ id }: { id: string }) {
  const Icon = RAIL_ITEM_ICON_COMPONENTS[id]
  return Icon ? <Icon /> : null
}

// Drawer tabs. Each mark is chosen to be readable at 17px and to not collide with its
// neighbours: the two navigators are the classic folder/document pair, and the two tabs that
// are both a trace (Activity and Processes) are held apart by silhouette — an open squiggle
// against a monitor on a stand — rather than by a detail that dissolves at this size.

/** A clipboard holding a clock: the paste surface, but the *history* of it. */
export const ClipboardHistoryIcon = () => <svg {...stroke}>
  <rect x="9" y="2" width="6" height="4" rx="1" />
  <path d="M15 4h2a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
  <circle cx="12" cy="13" r="4" />
  <path d="M12 11v2.2l1.5.9" />
</svg>

/** The command-key glyph: the four-looped square every keyboard shortcut is drawn with.
 *
 * Replaces a terminal window, which said "terminal" rather than "the keys and commands you
 * fire at one" — and said it in the same rectangle the Processes monitor is drawn in, two
 * tabs apart on the same rail. The loop square is the one mark in the set that means
 * *command* on its own, and its silhouette collides with nothing else here. */
export const CommandKeyIcon = () => <svg {...stroke}>
  <path d="M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3" />
</svg>

/** A message with body text: saved wording you send, not a discrete key action. */
export const PromptsIcon = () => <svg {...stroke}>
  <path d="M21 14a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z" />
  <line x1="8" y1="8" x2="17" y2="8" />
  <line x1="8" y1="12" x2="14" y2="12" />
</svg>

/** Three left-aligned rules with a clock face at the bottom-right: things written down in
 *  order, and the wait before the next one goes.
 *
 * The rules shorten as they descend so the stack reads as a list rather than a hamburger,
 * and the clock is what distinguishes the queue from every other list in the set — a queued
 * message is one that has not been delivered *yet*. It is the same face `ScheduleIcon` and
 * `HistoryIcon` draw, deliberately: three surfaces about time share one clock. */
export const QueueClockIcon = () => <svg {...stroke}>
  <line x1="3" y1="4.5" x2="20" y2="4.5" />
  <line x1="3" y1="9.5" x2="20" y2="9.5" />
  <line x1="3" y1="14.5" x2="10.5" y2="14.5" />
  <circle cx="16.8" cy="17.2" r="4.4" />
  <path d="M16.8 14.8v2.4l1.7 1.1" />
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

/** A pulse trace: the Activity tab, which is a session's turns and findings over time.
 *
 * A lightbulb said "idea", which is what a *finding* is and not what the tab is — the tab is
 * the record of what this session did, and a trace is the mark for that.
 *
 * The trace is also what `ProcessesIcon` draws, two tabs away on the same rail, so the two
 * are deliberately held apart by silhouette rather than by detail: this one is a bare,
 * full-bleed squiggle and that one is a framed monitor on a stand. An open line against a
 * closed rectangle survives 17px; two traces differing only in their frame would not. */
export const ActivityIcon = () => <svg {...stroke}>
  <path d="M2 12.5h4l2.5-7 3.5 12 2.5-5H22" />
</svg>

/** A node with two dependents hanging off it: one changed file and what it reaches.
 *
 * Deliberately not the Git fork — that mark is about branches, and this tab is about the
 * import graph. The filled centre node is the edited file; the two it feeds are the blast
 * radius, which is the one relationship the map exists to draw. */
export const ChangeMapIcon = () => <svg {...stroke}>
  <circle cx="6" cy="12" r="2.5" fill="currentColor" />
  <circle cx="18" cy="5" r="2" />
  <circle cx="18" cy="19" r="2" />
  <path d="M8.2 10.7 16 6.2M8.2 13.3 16 17.8" />
</svg>

/** A robot head: the tab about the CLI agent itself — its context, environment, and skills.
 *
 * A hexagonal "core" with ports was an abstraction of an agent; this is the thing. Antenna,
 * two eyes, and the two ear-nubs are what make a rounded rectangle read as a face rather
 * than as one more panel at 17px, which is the size this has to survive. */
export const RobotIcon = () => <svg {...stroke}>
  <line x1="12" y1="3.2" x2="12" y2="7" />
  <circle cx="12" cy="2.2" r="1.2" fill="currentColor" stroke="none" />
  <rect x="4.5" y="7" width="15" height="12" rx="3" />
  <line x1="4.5" y1="12" x2="2.2" y2="12" />
  <line x1="19.5" y1="12" x2="21.8" y2="12" />
  <circle cx="9.5" cy="13" r="1.25" fill="currentColor" stroke="none" />
  <circle cx="14.5" cy="13" r="1.25" fill="currentColor" stroke="none" />
</svg>

/** A folder. A folder *tree* is the truer picture but turns to mud at this size. */
export const FilesIcon = () => <svg {...stroke}>
  <path d="M3 7a2 2 0 0 1 2-2h3.6a2 2 0 0 1 1.7.9L11.5 8H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
</svg>

/** A written page with a pencil across its lower-right corner: notes are documents you
 *  *write*, which is the half a plain page left out.
 *
 * The page is drawn open at the corner the pencil crosses rather than behind it, because two
 * closed outlines overlapping at this size read as one smudge. */
export const NotePencilIcon = () => <svg {...stroke}>
  <path d="M17.5 11.5V7.5L13 3H6.5a1.8 1.8 0 0 0-1.8 1.8v14.4A1.8 1.8 0 0 0 6.5 21h4.2" />
  <polyline points="13 3 13 7.5 17.5 7.5" />
  <line x1="8" y1="11.5" x2="13" y2="11.5" />
  <path d="M19.7 12.7a1.6 1.6 0 0 1 2.3 2.3l-5.4 5.4-3 .7.7-3z" />
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

/** A pulse on a monitor: something running, and whether it is doing anything.
 *
 * Not a gauge or a chip — this tab is about live activity per session, and the trace is the one
 * mark that reads as "running" rather than "capacity".
 *
 * The stand is not decoration. `ActivityIcon` is also a trace and sits on the same rail, so
 * the frame alone was the whole difference between them; a frame *with a stand* is a monitor
 * and reads as a different object at a glance, which is what a 17px rail needs. */
export const ProcessesIcon = () => <svg {...stroke}>
  <rect x="2" y="4" width="20" height="13" rx="2" />
  <path d="M5.5 11.5h2.5l2-3.5 2.5 6 2-2.5h3" />
  <line x1="12" y1="17" x2="12" y2="20.5" />
  <line x1="8.5" y1="20.5" x2="15.5" y2="20.5" />
</svg>

/** A clock with a forward hand: a session this Project will start later.
 *
 *  Deliberately not a calendar. A calendar reads as "dates" and this tab is about
 *  a recurrence that mostly has no date - "every night at 3" is a clock face. */
export const ScheduleIcon = () => <svg {...stroke}>
  <circle cx="12" cy="12" r="9" />
  <path d="M12 7v5l3.5 2.5" />
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

/** A double chevron pointing down: open everything. The accordion mark — one chevron says
 *  "this row", two say "all of them".
 *
 *  The PROJECTS header used to say this with `⊞`/`⊟`, the box-drawing pair for a single
 *  *tree node*: nobody reads it as a bulk control, and at 11px the two differ by one
 *  hairline stroke. The obvious replacement is Material's `unfold_more`/`unfold_less`
 *  (chevrons diverging and converging), and it is wrong here — at the 14px this row runs,
 *  two converging chevrons with round joins render as an ✕. Two *parallel* chevrons keep
 *  the pair distinguishable by direction alone, which survives any size. */
export const UnfoldMoreIcon = () => <svg {...stroke}>
  <path d="M6 5l6 6 6-6" />
  <path d="M6 13l6 6 6-6" />
</svg>

/** `UnfoldMoreIcon` pointing the other way: fold everything shut. */
export const UnfoldLessIcon = () => <svg {...stroke}>
  <path d="M6 11l6-6 6 6" />
  <path d="M6 19l6-6 6 6" />
</svg>

/** A cogwheel: the registry behind the tree, where a Project's own record is edited. */
export const CogIcon = () => <svg {...stroke}>
  <circle cx="12" cy="12" r="7.6" />
  <circle cx="12" cy="12" r="2.8" />
  <path d="M12 1.8v2.6M12 19.6v2.6M22.2 12h-2.6M4.4 12H1.8M19.2 4.8l-1.9 1.9M6.7 17.3l-1.9 1.9M19.2 19.2l-1.9-1.9M6.7 6.7 4.8 4.8" />
</svg>

/** A question mark in a ring: the help surface. The one row in the app menu that
 *  explains rather than opens, so it wears the universal mark rather than a noun. */
export const HelpIcon = () => <svg {...stroke}>
  <circle cx="12" cy="12" r="9" />
  <path d="M9.3 9.2a2.8 2.8 0 1 1 3.4 3.1c-.6.2-.9.7-.9 1.3v.6" />
  <line x1="12" y1="17.2" x2="12" y2="17.3" />
</svg>

/** A plus. Add one more of the thing this surface lists. */
export const PlusIcon = () => <svg {...stroke}>
  <line x1="12" y1="5" x2="12" y2="19" />
  <line x1="5" y1="12" x2="19" y2="12" />
</svg>

/** A magnifier: filter the thing this surface lists down to what you type. Drawn
 *  slightly small in its box so the handle clears the edge at the 17px the sidebar
 *  header renders these at, where a full-bleed glyph loses the handle to the crop. */
export const SearchIcon = () => <svg {...stroke}>
  <circle cx="10.5" cy="10.5" r="6" />
  <line x1="15" y1="15" x2="20" y2="20" />
</svg>

// App-menu marks. The sidebar's `menu` and its right-click twin used to be pure text rows,
// which the terminal skin prefixed with one `> ` per row: a marker that is the same on every
// row says only "this is a menu row", so a reader scanned fifteen identical prefixes and read
// every label to find one entry. These replace it per row. They are sized at the menu's own
// 14px and share the drawer set's stroke weight, so a mark that appears in both places (Notes,
// Queue, Actions, Alerts) is literally the same drawing.

/** A clock running backwards: what already happened, not what is scheduled. Deliberately the
 *  mirror of `ScheduleIcon`'s forward hand - the two are the same face, and the direction of
 *  the arrow is the whole difference between "ran" and "will run". */
export const HistoryIcon = () => <svg {...stroke}>
  <path d="M3.5 12a8.5 8.5 0 1 0 2.7-6.2" />
  <polyline points="3 3.5 3 9 8.5 9" />
  <path d="M12 7.6V12l3.2 1.9" />
</svg>

/** A gauge with its needle: a dashboard, which is a reading rather than a document. The pivot
 *  is filled — an arc and a diagonal alone read as an abstract swoosh at 14px, and the hub is
 *  the one mark that fixes them as a dial. */
export const DashboardIcon = () => <svg {...stroke}>
  <path d="M3.4 17.5a8.6 8.6 0 1 1 17.2 0" />
  <path d="M12 17.5 16.3 11" />
  <circle cx="12" cy="17.5" r="1.3" fill="currentColor" stroke="none" />
</svg>

/** Three bars of different heights over a baseline: spend, which is a quantity compared
 *  against other quantities. Deliberately not a coin or a currency glyph — one of the three
 *  pots behind this row is a percentage of a provider window and is not money at all, and a
 *  dollar sign would promise a dialog that only holds two thirds of what it opens. */
export const SpendIcon = () => <svg {...stroke}>
  <line x1="3.5" y1="20" x2="20.5" y2="20" />
  <rect x="5" y="12.5" width="3.6" height="7.5" />
  <rect x="10.2" y="7" width="3.6" height="13" />
  <rect x="15.4" y="4" width="3.6" height="16" />
</svg>

/** A wrench: the group holding the reload and rebuild controls. Maintenance is the one entry
 *  here that is a category rather than a destination, and a tool is how a menu says so. */
export const WrenchIcon = () => <svg {...stroke}>
  <path d="M20.4 5.4 17 8.8l-2-2 3.4-3.4a5.2 5.2 0 0 0-6.9 6.3l-6.9 6.9a2 2 0 0 0 2.8 2.8l6.9-6.9a5.2 5.2 0 0 0 6.1-7.1z" />
</svg>

/** A circular arrow: reload the thing you are looking at. */
export const RefreshIcon = () => <svg {...stroke}>
  <path d="M20.5 12a8.5 8.5 0 1 1-2.7-6.2" />
  <polyline points="21 3.5 21 9 15.5 9" />
</svg>

/** Two stacked rack units: the daemon, as distinct from the page in front of you. Both rows
 *  reload, so the marks have to carry which *thing* reloads; a second circular arrow would
 *  have said "reload" twice and named neither. */
export const ServerIcon = () => <svg {...stroke}>
  <rect x="3" y="4" width="18" height="7" rx="1.6" />
  <rect x="3" y="13" width="18" height="7" rx="1.6" />
  <line x1="6.5" y1="7.5" x2="8.5" y2="7.5" />
  <line x1="6.5" y1="16.5" x2="8.5" y2="16.5" />
</svg>

/** A sealed box: the frozen desktop bundle, which redeploy rebuilds and swaps whole. */
export const PackageIcon = () => <svg {...stroke}>
  <path d="M21 8.4v7.2a1.6 1.6 0 0 1-.85 1.41l-7.4 4.05a1.6 1.6 0 0 1-1.5 0l-7.4-4.05A1.6 1.6 0 0 1 3 15.6V8.4a1.6 1.6 0 0 1 .85-1.41l7.4-4.05a1.6 1.6 0 0 1 1.5 0l7.4 4.05A1.6 1.6 0 0 1 21 8.4z" />
  <polyline points="3.4 7.7 12 12.4 20.6 7.7" />
  <line x1="12" y1="12.4" x2="12" y2="21.4" />
</svg>

/** Concentric waves off a core: one keystroke reaching several sessions. */
export const BroadcastIcon = () => <svg {...stroke}>
  <circle cx="12" cy="12" r="2" />
  <path d="M8.6 8.6a4.8 4.8 0 0 0 0 6.8M15.4 8.6a4.8 4.8 0 0 1 0 6.8" />
  <path d="M5.7 5.7a8.9 8.9 0 0 0 0 12.6M18.3 5.7a8.9 8.9 0 0 1 0 12.6" />
</svg>

/** One card behind another: a Group, which is Projects held together rather than a Project. */
export const GroupIcon = () => <svg {...stroke}>
  <rect x="3" y="8" width="13" height="12" rx="2" />
  <path d="M8 8V6a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2" />
</svg>

// Context-menu marks. The Project and session right-click menus used to be pure text rows
// under the terminal skin's one `> ` prefix, which says "this is a menu row" fifteen times
// and distinguishes nothing — the same problem the app menu solved above, in the two menus
// people open most. Each mark below names one act. They share the stroke weight of the set
// above so a concept drawn in both places (history, notes, groups, settings) is one drawing.

/** A pencil: rename the thing this menu is about. */
export const RenameIcon = () => <svg {...stroke}>
  <path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z" />
  <line x1="14.5" y1="6.5" x2="17.5" y2="9.5" />
</svg>

/** A bin: the thing is removed from swe-mux, not merely closed. */
export const TrashIcon = () => <svg {...stroke}>
  <path d="M4 7h16" />
  <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
  <path d="M6 7v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7" />
  <line x1="10" y1="11" x2="10" y2="17" />
  <line x1="14" y1="11" x2="14" y2="17" />
</svg>

/** The bin with two more receding behind it: clear *every* ended row in this Project, not
 *  only the one the menu was opened on. Split from `TrashIcon` for the reason
 *  `CopyPathIcon` was split from `CopyIcon` — the two sit on adjacent rows of one menu,
 *  and the same bin drawn twice would leave the icon column silent about which of them
 *  removes one row and which removes all of them. */
export const TrashSweepIcon = () => <svg {...stroke}>
  <path d="M2.5 8h12" />
  <path d="M6.5 8V6.2a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1V8" />
  <path d="M4.5 8v10a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2V8" />
  <line x1="17.5" y1="8.5" x2="17.5" y2="17.5" />
  <line x1="21" y1="10.5" x2="21" y2="15.5" />
</svg>

/** An eye struck through: still registered, just not drawn in the sidebar. Deliberately not
 *  the bin — hiding a Project loses nothing, and a mark that suggested otherwise would make
 *  the safe act look like the destructive one directly below it. */
export const HideIcon = () => <svg {...stroke}>
  <path d="M2.5 12S6 5.5 12 5.5c1.6 0 3 .45 4.2 1.1M21.5 12S18 18.5 12 18.5c-1.6 0-3-.45-4.2-1.1" />
  <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  <line x1="3" y1="3" x2="21" y2="21" />
</svg>

/** A folder with an arrow leaving it: hand this directory to the OS file manager. */
export const RevealIcon = () => <svg {...stroke}>
  <path d="M3 8a2 2 0 0 1 2-2h3.6a2 2 0 0 1 1.7.9L11.5 9H19a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  <path d="M12 16v-4.5M12 11.5 9.8 13.7M12 11.5l2.2 2.2" />
</svg>

/** A tick: accept the request this session is holding. */
export const CheckIcon = () => <svg {...stroke}>
  <polyline points="4 12.5 9.5 18 20 6" />
</svg>

/** A shield struck through: stop standing in for the human on approvals here. */
export const ShieldOffIcon = () => <svg {...stroke}>
  <path d="M5.5 6.4 12 3.5l6.5 2.9v5.2c0 3.4-2.5 6.4-6.5 8-1.3-.5-2.4-1.2-3.3-2" />
  <line x1="3" y1="3" x2="21" y2="21" />
</svg>

/** An envelope: the read/unread mark this session carries. */
export const MailIcon = () => <svg {...stroke}>
  <rect x="3" y="5" width="18" height="14" rx="2" />
  <polyline points="3.5 7 12 13 20.5 7" />
</svg>

/** A play triangle over a fresh line: start this conversation again as a new session. */
export const ResumeIcon = () => <svg {...stroke}>
  <path d="m9 7 8 5-8 5z" />
  <line x1="4" y1="4" x2="4" y2="20" />
</svg>

/** A small starburst: something the model generates rather than something you type. */
export const SparkleIcon = () => <svg {...stroke}>
  <path d="M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9z" />
  <path d="M18.5 16.5 19.2 18.5 21 19.2 19.2 20 18.5 22 17.8 20 16 19.2 17.8 18.5z" />
</svg>

/** A circle with a slash: clear the standing mark, without ending anything. */
export const ClearIcon = () => <svg {...stroke}>
  <circle cx="12" cy="12" r="8.5" />
  <line x1="6.5" y1="17.5" x2="17.5" y2="6.5" />
</svg>

/** The power glyph: end this process. Paired with `TrashIcon`, which the same control wears
 *  once the session has already ended and only its row is left to remove. */
export const PowerIcon = () => <svg {...stroke}>
  <path d="M7.8 6.6a7.5 7.5 0 1 0 8.4 0" />
  <line x1="12" y1="3" x2="12" y2="12" />
</svg>

/** A speaker with one wave: spoken replies for this session. */
export const SpeakerIcon = () => <svg {...stroke}>
  <path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4z" />
  <path d="M16 9.2a4 4 0 0 1 0 5.6" />
</svg>

/** The copy mark over a folder: copy the *path*, not the session's identifier. Split from
 *  `CopyIcon` for the same reason `CopyIcon` is not reused — the two sit adjacent in one menu. */
export const CopyPathIcon = () => <svg {...stroke}>
  <path d="M3 8a2 2 0 0 1 2-2h2.6a2 2 0 0 1 1.7.9L10.2 8H13a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  <rect x="11" y="11" width="10" height="10" rx="2" />
</svg>

/** A cross: back out of the confirmation this menu just opened. */
export const CloseIcon = () => <svg {...stroke}>
  <line x1="6" y1="6" x2="18" y2="18" />
  <line x1="18" y1="6" x2="6" y2="18" />
</svg>

/** Every drawer tab must appear here; the strip and the rail both read this map. */
export const DRAWER_TAB_ICONS: Record<DrawerTabId, () => VNode> = {
  actions: CommandKeyIcon,
  queue: QueueClockIcon,
  transcript: TranscriptIcon,
  activity: ActivityIcon,
  agent: RobotIcon,
  files: FilesIcon,
  notes: NotePencilIcon,
  git: GitIcon,
  processes: ProcessesIcon,
  schedule: ScheduleIcon,
  notifications: AlertsIcon,
}

// `ClipboardHistoryIcon`, `ChangeMapIcon`, and `ContextIcon` are no longer drawer marks —
// their surfaces are segments and a section now — but they stay exported: Change Map keeps
// its own workspace-tab glyph, and the other two label their segment and section controls.
