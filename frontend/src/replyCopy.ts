export type ReplyIdentity = {
  id: string
  native_session_id: string
  agent_run_id?: string | null
  turn_epoch?: number
}

export type ReplySnapshot = {
  session_id: string
  native_session_id: string
  agent_run_id: string | null
  turn_epoch: number
  message_id: string
  turn_id: string | null
  revision: string
  text: string
  previous_answer: boolean
}

export function replyIdentity(session: ReplyIdentity): string {
  return JSON.stringify([session.id, session.native_session_id, session.agent_run_id ?? null, session.turn_epoch ?? 0])
}

/** Every click reads fresh. A later click or conversation switch retires its request. */
export class ReplyCopyRequest {
  private generation = 0
  private identity = ''

  observe(session: ReplyIdentity): void {
    const identity = replyIdentity(session)
    if (identity !== this.identity) {
      this.identity = identity
      this.generation++
    }
  }

  cancel(): void { this.generation++ }

  async load(session: ReplyIdentity, read: () => Promise<ReplySnapshot>): Promise<ReplySnapshot | null> {
    this.observe(session)
    const generation = ++this.generation
    const identity = this.identity
    let snapshot: ReplySnapshot
    try { snapshot = await read() } catch (error) {
      if (generation !== this.generation) return null
      throw error
    }
    if (generation !== this.generation || identity !== this.identity) return null
    if (replyIdentity({...snapshot, id: snapshot.session_id}) !== identity) {
      throw new Error('The conversation changed. Try Copy again.')
    }
    if (!snapshot.text || !snapshot.message_id || !snapshot.revision) {
      throw new Error('No completed assistant answer is available yet.')
    }
    return snapshot
  }
}
