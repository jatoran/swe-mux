import { t } from './localization/index.ts'
/**
 * The prompt an operator hands an agent to set up worktree verification **here**.
 *
 * It is drawn on the repository that needs it, which is what the strip's own copy now
 * says: the earlier "setting this up in another repository" framing named the wrong
 * repository, and the prompt's first line has always said "this repository".
 *
 * Auto-merge's whole value is that a machine runs the gate, and a repository with no
 * verification command gets nothing from it: `verify` refuses rather than runs, and every
 * land refuses with it. Writing that command is real work in an unfamiliar repository -
 * which suite is the full one, what it must not collide with, what its exit code has to
 * mean - and all of it is already written down here, in this document and in the docs the
 * strip points at. So the strip hands it over as text rather than making each operator
 * reconstruct it from memory in a new repo.
 *
 * **It is a proposal, and the last paragraph is what keeps it one.** The prompt ends by
 * telling the receiving agent that it cannot approve what it just wrote, because a
 * verification command is authority - it decides what reaches a trunk unattended - and
 * approval is a human act against the exact bytes, made in this very section afterwards
 * (`land-queue.md`, "Editing never approves"). Without that ending, a copyable prompt is
 * a nicer way of saying "an agent set up its own gate", which is the one thing the
 * approval model exists to prevent. It is not a mechanism - the daemon enforces the
 * separation whatever any prompt says - but a prompt that omitted it would be sending an
 * agent off to do work whose last step it is not allowed to take, without saying so.
 *
 * Deliberately not fetched from the daemon. Every fact in it is a property of the land
 * queue's design rather than of this install, the strip already holds the one variable
 * (the script convention's name), and a template is the thing most likely to be edited by
 * whoever is reading the section it sits in.
 */

/** The conventional override key's file, relative to the repository root. */
const CONFIG_PATH = '.swe-mux/config.toml'

/**
 * A ready-to-paste prompt for setting up verification in another repository.
 *
 * `scriptName` is the executable-file convention as this install resolves it, so the
 * prompt names the same file the reader's own strip is talking about rather than a
 * hard-coded one.
 */
export function verifySetupPrompt(scriptName: string): string {
  const script = scriptName || '.worktree-verify'
  return t("landSetupPrompt.00260fb4fc", { value1: script, value2: CONFIG_PATH })
}
