import { availableDrawerSegments, type DrawerSegmentContext } from './drawerSegments'
import type { DrawerTabId } from './drawerTabs'

// The one segmented control in the drawer.
//
// Every segmented tab draws this, in the same place, from the same registry
// (`drawerSegments.ts`) — which is the point. Insight and Git each grew their own toggle
// with its own markup, its own class name, and its own local `useState`, and the third one
// would have been a third. Consolidating tabs into segments made that duplication load
// bearing: the control has to sit somewhere predictable if a segment is going to be as
// reachable as the tab it replaced.
//
// Unavailable segments are dropped rather than disabled. A disabled tab-shaped control is
// a promise the surface cannot keep — "Timeline" greyed out on a shell session says the
// session has a timeline you are not allowed to see — and `resolveDrawerSegment` has
// already picked something available to show instead.

type Props = {
  tab: DrawerTabId
  active: string | null
  context: DrawerSegmentContext
  onSelect: (segment: string) => void
}

export function DrawerSegmentControl({ tab, active, context, onSelect }: Props) {
  const segments = availableDrawerSegments(tab, context)
  // One available segment is not a choice, so it is not drawn as one. This is the Agent tab
  // on a shell session (Instructions alone) rather than a hypothetical.
  if (segments.length < 2) return null
  return <div class="drawer-segmented" role="tablist" aria-label={`${tab} view`}>
    {segments.map((segment, index) => <button
      key={segment.id}
      role="tab"
      aria-selected={segment.id === active}
      class={segment.id === active ? 'active' : ''}
      title={segment.title}
      tabIndex={segment.id === active ? 0 : -1}
      onClick={() => onSelect(segment.id)}
      onKeyDown={event => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault()
        const offset = event.key === 'ArrowLeft' ? -1 : 1
        onSelect(segments[(index + offset + segments.length) % segments.length].id)
      }}
    >{segment.label}</button>)}
  </div>
}
