// How one lineage edge reads to a human looking at one conversation.
//
// The section this feeds sits on a single History entry, so an edge is never a
// symmetric "A → B": it is either "this conversation came from that one" or "that one
// came from this". Saying which, in the direction the reader is standing, is most of
// what makes the difference between a lineage section and two uuids and an arrow.
//
// Pure and daemon-fed. The names, and whether each end still exists, are resolved by
// `GET /api/lineage` because only the daemon can see live sessions, History rows, and
// deleted rows at once. Nothing here recomputes them.

export type LineageEndpoint = Readonly<{
  name: string
  /** A session is still open on this run, so it can be focused rather than resumed. */
  live: boolean
  /** False when the run's History row is gone. The edge still records the fork. */
  known: boolean
  session_id?: string
}>

export type LineageEdge = Readonly<{
  id: string
  parent_run_id: string
  child_run_id: string
  relation: string
  created_at: number
  parent?: LineageEndpoint
  child?: LineageEndpoint
  metadata?: Readonly<Record<string, unknown>>
}>

/** Which end of an edge the conversation on screen is. */
export type LineageDirection = 'from' | 'to'

export const lineageDirection = (edge: LineageEdge, runId: string): LineageDirection =>
  edge.child_run_id === runId ? 'from' : 'to'

/** The other end of the edge, relative to the conversation on screen. */
export const lineageCounterpart = (
  edge: LineageEdge,
  runId: string,
): { endpoint: LineageEndpoint; runId: string } => {
  const other = lineageDirection(edge, runId) === 'from' ? 'parent' : 'child'
  return {
    endpoint: edge[other] || { name: '', live: false, known: false },
    runId: other === 'parent' ? edge.parent_run_id : edge.child_run_id,
  }
}

const RELATION_VERB: Record<string, { from: string; to: string }> = {
  branch: { from: 'Branched from', to: 'Branched into' },
  resume: { from: 'Resumed from', to: 'Resumed into' },
  handoff: { from: 'Handed off from', to: 'Handed off to' },
  continuation: { from: 'Continues', to: 'Continued by' },
  review: { from: 'Reviews', to: 'Reviewed by' },
}

/** What this edge did, said in the direction the reader is standing. */
export function lineageVerb(relation: string, direction: LineageDirection): string {
  const verbs = RELATION_VERB[relation]
  if (verbs) return verbs[direction]
  // An unrecognised relation still reads as itself rather than vanishing: the set is
  // the daemon's and can grow without this table.
  return direction === 'from' ? `${relation} from` : `${relation} to`
}

/** What the other end is called, or why it has no name. */
export function lineageEndpointLabel(endpoint: LineageEndpoint, runId: string): string {
  if (!endpoint.known) return 'conversation removed'
  return endpoint.name || runId
}

/** Where a branch was cut, from what the edge kept. `''` when it kept nothing.
 *
 * Branches made before the transcript-fork rewrite carry no cut at all: the fork was
 * the CLI's and mux never saw a message id, so the honest rendering is the relation
 * alone rather than an invented position. */
export function lineageCutLabel(metadata: Readonly<Record<string, unknown>> | undefined): string {
  if (!metadata) return ''
  const mode = typeof metadata.mode === 'string' ? metadata.mode : ''
  const text = typeof metadata.from_message_text === 'string' ? metadata.from_message_text : ''
  const role = typeof metadata.from_message_role === 'string' ? metadata.from_message_role : ''
  if (!mode) return ''
  const speaker =
    role === 'user' ? 'your message' : role === 'assistant' ? 'the reply' : 'the message'
  const where = mode === 'before' ? `before ${speaker}` : `after ${speaker}`
  return text ? `${where} “${text}”` : where
}

/** Newest first: a conversation's most recent relative is the one being looked for. */
export function orderedLineage(edges: readonly LineageEdge[]): LineageEdge[] {
  return [...edges].sort((left, right) => right.created_at - left.created_at)
}
