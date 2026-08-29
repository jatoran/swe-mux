import { useEffect, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import { SettingLink } from './SettingLink'
import { SETTING_TARGETS } from './settingTargets'
import {
  applyGrants,
  automationClosure,
  GRANTS,
  GRANT_SCOPE_LABEL,
  grantKey,
  grantWrittenTo,
  type GrantDescriptor,
  type GrantId,
  type GrantResult,
} from './grants'
import { fetchProjectAutomations, type ProjectAutomationState } from './projectAutomations'
import { fetchLlmProvider, type LlmReadiness } from './llmProvider'

/**
 * The one shape a surface takes when it cannot work until something is switched on.
 *
 * It says what is off, what turning it on would do, exactly what turning it on would
 * change, and offers one button that does it here. That is the Land queue's verification
 * gate - state the block, show the bytes, approve in place - applied to every other
 * switch, and it replaces a link that opened an overlay, staged a Save, and left the
 * reader to walk back to the pane they started on.
 *
 * The deep link stays, as the secondary control. A gate answers the question in front of
 * you; the overlay is still where you go to see the whole picture or to turn something
 * back off, which a gate deliberately cannot do.
 *
 * A gate is rendered only while the surface is inert, and disappears the moment it is
 * not. That is what keeps a Project-wide control from becoming a standing fixture in a
 * session-scoped pane, which is the reason the scan timeline's Project permission was
 * taken out of the Timeline tab in the first place (`test/scanTimeline.test.ts`).
 */
export function GrantGate({
  ids,
  projectId,
  heading,
  children,
  confirmLabel,
  onGranted,
  applyDevice,
  extra,
}: {
  /** The switches that are off, outermost first. Applied together, as one act. */
  ids: GrantId[]
  /** Required when any id is Project-scoped; a gate without one refuses rather than
   *  guessing which Project the operator meant. */
  projectId?: string
  /** What is off, as a statement. One sentence, in bold. */
  heading: string
  /** What turning it on would do. The consequence, not a restatement of the heading. */
  children?: ComponentChildren
  /** Overrides the derived button text where the natural phrasing is not "Turn on X". */
  confirmLabel?: string
  /** Called after a successful grant so the host reloads its own state immediately,
   *  rather than a websocket round trip later. */
  onGranted?: (result: GrantResult) => void | Promise<void>
  /** Device-scoped grants are a local write only the host knows how to make. */
  applyDevice?: (id: GrantId) => void | Promise<void>
  /** Anything the surface needs to add below the disclosure and above the button. */
  extra?: ComponentChildren
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [registry, setRegistry] = useState<ProjectAutomationState['automations'] | null>(null)
  const [llm, setLlm] = useState<LlmReadiness | null>(null)

  const automationIds = ids
    .filter(id => {
      const grant: GrantDescriptor = GRANTS[id]
      return grant.scope === 'project' && grant.kind === 'automation'
    })
    .map(grantKey)

  // The registry is only needed to name a dependency closure, so it is fetched only when
  // there is one to name. A gate over install switches alone makes no request at all.
  useEffect(() => {
    if (!automationIds.length || !projectId) { setRegistry(null); return }
    let stale = false
    fetchProjectAutomations(projectId)
      .then(state => { if (!stale) setRegistry(state.automations) })
      .catch(() => { if (!stale) setRegistry(null) })
    return () => { stale = true }
  }, [projectId, automationIds.join(',')])

  const needsProject = ids.some(id => GRANTS[id].scope === 'project')
  const missingProject = needsProject && !projectId
  const closure = registry ? automationClosure(automationIds, registry) : automationIds
  const byId = new Map((registry || []).map(item => [item.id, item]))
  const dragged = closure.filter(id => !automationIds.includes(id))
  const spends = closure.some(id => byId.get(id)?.spends === true)
    || ids.some(id => GRANTS[id].scope === 'install'
      && ['automation_enabled', 'scan_timeline_enabled'].includes(grantKey(id)))
  const wantsModel = closure.some(id => byId.get(id)?.needs_llm === true)
    || ids.some(id => GRANTS[id].scope === 'install'
      && ['automation_enabled', 'scan_timeline_enabled'].includes(grantKey(id)))

  // Only asked when the answer would change what this gate says. A gate over free,
  // model-free switches makes no request at all, exactly as it makes none for the
  // registry when it has no closure to name.
  useEffect(() => {
    if (!wantsModel) { setLlm(null); return }
    let stale = false
    fetchLlmProvider()
      .then(status => { if (!stale) setLlm(status.llm) })
      .catch(() => { if (!stale) setLlm(null) })
    return () => { stale = true }
  }, [wantsModel])

  const primary = ids[0]
  const label = confirmLabel
    || (ids.length === 1
      ? `Turn on ${SETTING_TARGETS[primary].label}`
      : `Turn on ${SETTING_TARGETS[ids[ids.length - 1]].label}`)

  const grant = async () => {
    setBusy(true); setError('')
    try {
      const result = await applyGrants({ ids, projectId, applyDevice })
      await onGranted?.(result)
    } catch (cause) {
      // A refusal names its own reason (`grants.py`); anything else is reported as it
      // arrived. Either way the gate stays up, because nothing changed.
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally { setBusy(false) }
  }

  return <div class="setting-gate grant-gate">
    <p><strong>{heading}</strong></p>
    {children}
    <ul class="grant-gate-steps">
      {ids.map(id => {
        const grantDescriptor: GrantDescriptor = GRANTS[id]
        return <li key={id}>
          <span class="grant-gate-scope">{GRANT_SCOPE_LABEL[grantDescriptor.scope]}</span>
          <span class="grant-gate-what">{SETTING_TARGETS[id].label}</span>
          <small>{grantWrittenTo(id)}</small>
        </li>
      })}
    </ul>
    {/* Named on the button, not discovered after it. Enabling the change map quietly
        enables two substrate automations, and an operator who read one label and got
        three things has been misled even though nothing went wrong. */}
    {dragged.length > 0 && <p class="grant-gate-closure">
      Also switches on {dragged.map(id => byId.get(id)?.label || id).join(', ')}, which
      {dragged.length === 1 ? ' it reads from' : ' they read from'}.
    </p>}
    <p class={spends ? 'grant-gate-cost spends' : 'grant-gate-cost'}>
      {spends
        ? 'This one calls a model, so it can spend against your automation budget.'
        : 'Free to run — it reads what swe-mux already has and never calls a model.'}
    </p>
    {/* The grant would land and the switch would still be inert, so this is disclosed
        before the press rather than discovered as an automation that never fires. The
        provider is a value rather than a switch - choosing one and typing a URL, key,
        and model is not something a button can honestly offer - so it links out to its
        owner instead of being granted here. */}
    {llm && !llm.ready && <p class="grant-gate-cost spends">
      {llm.reason}{' '}
      <SettingLink variant="link" target="accounts.llmProvider">
        Choose or verify a model provider
      </SettingLink>
    </p>}
    {extra}
    {error && <p class="grant-gate-error" role="alert">{error}</p>}
    <div class="grant-gate-actions">
      <button type="button" class="grant-gate-confirm" disabled={busy || missingProject}
        onClick={() => void grant()}>{busy ? 'Turning on…' : label}</button>
      {/* The owning overlay stays reachable. A gate answers this question; the overlay
          is where the whole picture lives and the only place it can be undone. */}
      <SettingLink variant="link" target={primary} projectId={projectId}
        title={`See this beside everything else in ${SETTING_TARGETS[primary].where}`}
      >Open {SETTING_TARGETS[primary].where}</SettingLink>
    </div>
    {missingProject && <p class="grant-gate-error">
      Select a Project first — that switch belongs to one Project.
    </p>}
  </div>
}

/**
 * One grant, inline inside a sentence, for a surface that is working and is merely
 * telling you about a switch.
 *
 * The full gate is for a surface that cannot function; this is for the prose that names
 * a switch in passing - "turn on arm every new conversation to stop repeating this" -
 * which the setting-links rule says obliges a control and which several such sentences
 * shipped without. It carries the same disclosure in its tooltip rather than as a block,
 * because a paragraph is not the place for a four-line consequence list.
 */
export function GrantButton({ id, projectId, children, onGranted, applyDevice, title }: {
  id: GrantId
  projectId?: string
  children?: ComponentChildren
  onGranted?: (result: GrantResult) => void | Promise<void>
  applyDevice?: (id: GrantId) => void | Promise<void>
  title?: string
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const needsProject = GRANTS[id].scope === 'project' && !projectId
  const grant = async () => {
    setBusy(true); setError('')
    try {
      const result = await applyGrants({ ids: [id], projectId, applyDevice })
      await onGranted?.(result)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally { setBusy(false) }
  }
  return <>
    <button type="button" class="setting-link inline grant-inline"
      disabled={busy || needsProject}
      title={title || `${SETTING_TARGETS[id].label} · writes to ${grantWrittenTo(id)}`}
      onClick={event => { event.stopPropagation(); void grant() }}
    >{busy ? 'Turning on…' : children || `Turn on ${SETTING_TARGETS[id].label}`}</button>
    {error && <span class="grant-gate-error" role="alert"> {error}</span>}
  </>
}

/**
 * One unavailable surface, blocked by one switch.
 *
 * A full `GrantGate` is necessary when enabling has a dependency closure, a provider
 * prerequisite, Project scope, or spend to disclose. A single switch needs none of that
 * vertical ceremony: state, consequence, action, and the owning settings page fit in one
 * compact flag. The same grant path still performs the write, so compactness changes only
 * presentation.
 */
export function CompactGrantFlag({ id, heading, consequence, projectId, confirmLabel,
  onGranted, applyDevice }: {
  id: GrantId
  heading: string
  consequence?: string
  projectId?: string
  confirmLabel?: string
  onGranted?: (result: GrantResult) => void | Promise<void>
  applyDevice?: (id: GrantId) => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const needsProject = GRANTS[id].scope === 'project' && !projectId
  const grant = async () => {
    setBusy(true); setError('')
    try {
      const result = await applyGrants({ ids: [id], projectId, applyDevice })
      await onGranted?.(result)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally { setBusy(false) }
  }
  return <div class="compact-grant-flag">
    <span class="compact-grant-mark" aria-hidden="true">⚑</span>
    <div class="compact-grant-copy"><strong>{heading}</strong>{consequence&&<small>{consequence}</small>}</div>
    <button type="button" class="compact-grant-confirm" disabled={busy||needsProject}
      onClick={()=>void grant()}>{busy?'Turning on…':confirmLabel||`Turn on ${SETTING_TARGETS[id].label}`}</button>
    <SettingLink variant="link" target={id} projectId={projectId}>Details</SettingLink>
    {needsProject&&<span class="compact-grant-error">Select a Project first.</span>}
    {error&&<span class="compact-grant-error" role="alert">{error}</span>}
  </div>
}
