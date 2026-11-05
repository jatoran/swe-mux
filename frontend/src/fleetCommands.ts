import { t } from './localization/index.ts'
import type { Command } from './commands.ts'
import { serverNow } from './serverClock.ts'
import { sessionLaunchVoicePhrases } from './voiceLaunch.ts'
import type { Project, Session } from './types.ts'

/**
 * What a fleet command does when it runs.
 *
 * Passed separately from the data because these must reach the *current* render:
 * the registry below is memoized on the fleet, so a captured handler would act on
 * the snapshot the memo was built from. The composition root satisfies this with a
 * ref-backed facade whose identity never changes.
 */
export type FleetCommandActions = {
  /** The numbered `project.activate(N)` shortcut: select the Project and its first view. */
  activateProject(projectId: string): void
  focusProject(projectId: string): void
  focusSession(session: Session): void
  spawnSession(projectId: string, backend: string, seedText?: string): Promise<unknown>
}

export type FleetCommandInput = {
  /** Sidebar reading order; the numbered shortcuts follow it. */
  displayProjects: readonly Project[]
  projects: readonly Project[]
  sessions: readonly Session[]
  activeProjectId: string
  /** Every launchable harness, from the installed registry. */
  backends: readonly string[]
  harnessDisplayName: (backend: string) => string
  sessionName: (session: Session) => string
  isEnded: (session: Session) => boolean
  actions: FleetCommandActions
}

/**
 * The identity of the sidebar order, as a value.
 *
 * `displayProjects` is derived fresh on every render, so memoizing on its identity
 * would never hit. Its *content* is pinned by the Project records (a separate
 * dependency) plus the order they appear in, which is what this captures.
 */
export function displayOrderKey(projects: readonly Project[]): string {
  return projects.map(project => project.id).join(' ')
}

/**
 * Spoken aliases that name a session by what it is doing rather than by its name.
 *
 * Time-sensitive by design ("the stuck one" is a session that has been working
 * without activity for five minutes), so it is re-derived whenever the fleet
 * snapshot is, which the daemon replaces on every refresh cycle.
 */
export function sessionVoiceAliases(session: Session): string[] {
  if (session.awaiting_reason === 'approval') return [t("fleetCommands.d6bf654ccf"), t("fleetCommands.c901d2ec4b"), t("fleetCommands.79339d2323")]
  if (session.awaiting_reason === 'question' || session.awaiting_reason === 'elicitation') return [t("fleetCommands.9f10272697"), t("fleetCommands.8d9d28ecf4")]
  if (session.awaiting_reason === 'rate_limit') return [t("fleetCommands.d999cf360b")]
  if (session.delivery_readiness?.state === 'unknown' || ((session.state === 'working' || session.state === 'running') && serverNow() - session.last_activity_ts > 300)) return [t("fleetCommands.af66119673")]
  if (session.state === 'working' || session.state === 'running') return [t("fleetCommands.2d043a394e")]
  if (session.state === 'idle') return [t("fleetCommands.26be1d8afd")]
  if (session.state === 'crashed') return [t("fleetCommands.ef8c355376")]
  return []
}

/**
 * The half of the command registry that scales with the fleet: one command per
 * numbered Project slot, per Project, per live session, and per Project-and-harness
 * launch pair.
 *
 * Split out of the composition root because it is the part worth memoizing - the
 * spoken-phrase generation alone builds ten strings per Project-and-harness pair, and
 * it was being rebuilt on every render, including every five-second clock tick. Its
 * inputs are the fleet and the sidebar order; the hand-written half of the registry
 * stays inline, where its `available` expressions can read live UI state directly.
 */
export function buildFleetCommands(input: FleetCommandInput): Command[] {
  const { displayProjects, projects, sessions, activeProjectId, backends, actions } = input
  const projectNumber = new Map(displayProjects.map((project, index) => [project.id, index + 1]))
  return [
    ...displayProjects.slice(0, 9).map((project, index): Command => ({
      id: `project.activate(${index + 1})`, get label() { return t("fleetCommands.20e64c838c", { value1: index + 1, name: project.name }) },
      category: 'project', available: project.id !== activeProjectId, get disabledReason() { return t("fleetCommands.a489dc9abb") },
      run: () => actions.activateProject(project.id),
    })),
    ...projects.map((project): Command => ({
      id: `project.focus:${project.id}`, get label() { return t("fleetCommands.43e3edc940", { name: project.name }) }, category: 'project', available: true,
      run: () => actions.focusProject(project.id),
      voice: { phrases: [`go to project ${project.name}`, `open project ${project.name}`, `switch to ${project.name}`] },
    })),
    ...sessions.filter(session => !session.pending && !input.isEnded(session)).map((session): Command => ({
      id: `session.focus:${session.id}`, get label() { return t("fleetCommands.30dee3b720", { value1: input.sessionName(session) }) }, category: 'session', available: true,
      run: () => void actions.focusSession(session),
      voice: { phrases: [`go to session ${input.sessionName(session)}`, `open session ${input.sessionName(session)}`, `focus ${input.sessionName(session)}`, ...sessionVoiceAliases(session)] },
    })),
    ...projects.flatMap(project => backends.map((backend): Command => ({
      id: `session.spawn:${project.id}:${backend}`, get label() { return t("fleetCommands.a98b6015e0", { value1: input.harnessDisplayName(backend), name: project.name }) }, category: 'session', available: true,
      run: () => void actions.spawnSession(project.id, backend),
      voice: {
        phrases: sessionLaunchVoicePhrases({
          backend,
          displayName: input.harnessDisplayName(backend),
          projectName: project.name,
          projectNumber: projectNumber.get(project.id) ?? null,
          currentProject: project.id === activeProjectId,
        }),
        execute: async text => {
          const started = await actions.spawnSession(project.id, backend, text || undefined)
          return { detail: started
            ? t("fleetCommands.a9a338a09c", { value1: input.harnessDisplayName(backend), value2: project.name, value3: (text) ? 'yes' : 'no' })
            : t("fleetCommands.762ec6d821") }
        },
      },
    }))),
  ]
}
