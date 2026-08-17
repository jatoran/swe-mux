// The prompt a branch cut out, held until the pane that should carry it can take it.
//
// Branching *before* one of your own messages exists so that message can be sent
// differently. Handing the words back is what makes editing them the obvious next act
// rather than retyping from memory - but the pane they belong in does not exist yet
// when the daemon returns them, and for the seconds after it spawns it is still
// replaying its scrollback and cannot take input.
//
// So the text is staged here and the pane claims it when it finishes replaying. It is
// inserted into the composer and deliberately **not** submitted: a branch that sent the
// prompt for you would have made the same request again, which is the one thing the
// operator branched in order not to do.
//
// Not the spawn's own `seed_text`, for that reason: that path appends the prompt to the
// CLI's argv, which runs it.

/** How many staged seeds are kept. A pane that never opens leaves one behind, so the
 *  map is bounded rather than trusted to drain. Small because a person branches one
 *  conversation at a time; the oldest is dropped first. */
export const MAX_STAGED_BRANCH_SEEDS = 8

const staged = new Map<string, string>()

/** Hold `text` for the pane that will carry it. Empty text stages nothing. */
export function stageBranchSeed(sessionId: string, text: string | null | undefined): void {
  if (!sessionId || !text) return
  staged.delete(sessionId)
  staged.set(sessionId, text)
  while (staged.size > MAX_STAGED_BRANCH_SEEDS) {
    const oldest = staged.keys().next()
    if (oldest.done) break
    staged.delete(oldest.value)
  }
}

/** The seed for this pane, removed as it is handed over. `''` when there is none.
 *
 * Taking rather than reading: a seed is inserted once. A pane that reconnects and
 * replays again must not re-insert a prompt the operator has already edited or sent. */
export function takeBranchSeed(sessionId: string): string {
  const text = staged.get(sessionId) || ''
  staged.delete(sessionId)
  return text
}

/** Drop a staged seed whose pane never came up. */
export function forgetBranchSeed(sessionId: string): void {
  staged.delete(sessionId)
}

/** Test seam: how many seeds are waiting. */
export const stagedBranchSeedCount = (): number => staged.size
