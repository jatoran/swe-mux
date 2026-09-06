import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
import {
  clearClientStorage, confirmationMatches, fetchFactoryResetPreview, requestFactoryReset,
  waitForDaemon,
  type FactoryResetPreview,
} from './factoryReset.ts'

/**
 * The confirmation for a factory reset, and the wait that follows it.
 *
 * Every fact it shows comes from the daemon (`GET /api/maintenance/factory-reset`),
 * for the reason `UpdateDialog` renders the daemon's plan rather than deriving
 * one: a second implementation of "what will this destroy" would eventually
 * disagree with the one that actually destroys it.
 *
 * The dialog's job is to make the consequence unmissable *before* the press, in
 * the order the consequences matter:
 *
 * - **Sessions end.** Named, not counted, and above everything else, with the
 *   ask to stop them first. A reset reaps every live agent and terminal, and
 *   the person pressing this is usually thinking about settings.
 * - **What survives.** Repositories, worktree checkouts and voice downloads are
 *   listed as kept, because the fear a reset produces is about the work, and an
 *   unanswered fear is what makes someone not press a button they wanted.
 * - **Where it goes.** Nothing is deleted; the data directory is moved into
 *   `.trash`. That is both the safety net and a real disk cost, so it is said
 *   here rather than discovered later in the storage report.
 *
 * Consent is typed, not clicked. The phrase is the daemon's, echoed back to it.
 */

type Props = { onClose: () => void }

type Stage =
  | { name: 'loading' }
  | { name: 'ready' }
  | { name: 'resetting'; note: string }
  | { name: 'error'; message: string }

export function FactoryResetDialog({ onClose }: Props) {
  const [preview, setPreview] = useState<FactoryResetPreview | null>(null)
  const [stage, setStage] = useState<Stage>({ name: 'loading' })
  const [typed, setTyped] = useState('')
  const [external, setExternal] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  // Closable until the daemon has accepted. After that the reset is happening
  // whatever this window does, and a dialog that could be dismissed mid-wipe
  // would leave the page talking to a daemon that is moving its own files.
  const closable = stage.name !== 'resetting'
  useModalFocus(ref, () => { if (closable) onClose() }, true, 'factory-reset-dialog')

  useEffect(() => {
    let cancelled = false
    void fetchFactoryResetPreview().then(loaded => {
      if (cancelled) return
      setPreview(loaded)
      setStage({ name: 'ready' })
    }).catch(cause => {
      if (!cancelled) setStage({ name: 'error', message: cause instanceof Error ? cause.message : String(cause) })
    })
    return () => { cancelled = true }
  }, [])

  const start = useCallback(async () => {
    if (!preview) return
    setStage({ name: 'resetting', note: 'Stopping sessions…' })
    try {
      const accepted = await requestFactoryReset(typed, external)
      if (accepted.clear_client_storage) {
        setStage({ name: 'resetting', note: 'Clearing this device…' })
        await clearClientStorage()
      }
      setStage({ name: 'resetting', note: 'Waiting for swe-mux to come back…' })
      if (await waitForDaemon()) { location.reload(); return }
      setStage({
        name: 'error',
        message: 'The daemon did not come back in time. It is most likely still resetting; '
          + 'reload this page, and check daemon.log in the data directory if it stays away.',
      })
    } catch (cause) {
      setStage({ name: 'error', message: cause instanceof Error ? cause.message : String(cause) })
    }
  }, [external, preview, typed])

  const phrase = preview?.confirmation_phrase || 'factory reset'
  const armed = !!preview && preview.relaunchable && preview.local
    && confirmationMatches(typed, phrase)
  const sessions = preview?.sessions || []

  return <div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label="Factory reset" onClick={() => { if (closable) onClose() }}>
    <div class="modal daemon-reload-modal factory-reset-dialog" ref={ref} onClick={event => event.stopPropagation()}>
      <h2>Reset swe-mux to a fresh install?</h2>

      {stage.name === 'loading' && <p>Reading what this would remove…</p>}

      {preview && <>
        <p class={`redeploy-interrupts${sessions.length ? ' redeploy-blocked' : ''}`}>
          <strong>{sessions.length
            ? `Every live session ends (${sessions.length}).`
            : 'No sessions are running.'}</strong>
          <span>{sessions.length
            ? 'Stop them yourself first if any agent is mid-run: this terminates them where they stand, and unsaved work in a terminal is lost.'
            : 'Nothing is running, so nothing is interrupted.'}</span>
        </p>
        {sessions.length > 0 && <ul class="factory-reset-sessions">
          {sessions.map(session => <li key={session.id}>
            <strong>{session.name || session.id}</strong>
            <small>{[session.project, session.backend, session.state].filter(Boolean).join(' · ')}</small>
          </li>)}
        </ul>}

        <p>Configuration, Projects, history, the database, notes, prompts, plugins, credentials
          and every log are moved out of <code>{preview.data_dir}</code> into its <code>.trash</code>
          folder ({preview.entries.length} items). Nothing is deleted, so the space stays used until
          you empty it - and the old install can be recovered by hand.</p>

        <p class="profile-hint"><strong>Left alone:</strong> files inside your repositories
          (<code>.swe-mux/</code> config, actions and notes stay exactly as they are),
          {preview.worktrees.length > 0
            ? ` your ${preview.worktrees.length} worktree checkout${preview.worktrees.length === 1 ? '' : 's'},`
            : ' worktree checkouts,'} downloaded voice models, and the shim directory on your PATH.</p>

        <label class="check" data-setting="factory_reset_external">
          <span>Also undo shortcuts and installed agent skills</span>
          <input type="checkbox" checked={external} disabled={stage.name === 'resetting'} onChange={event => setExternal(event.currentTarget.checked)} />
          <small>Removes the Start Menu, startup and desktop entries, and the swe-mux skill from
            <code>~/.claude</code> and <code>~/.agents</code> - only files carrying swe-mux's own
            marker. Off by default: each of those was a separate act you approved.</small>
        </label>

        {external && preview.external_left.length > 0 && <ul class="factory-reset-left">
          {preview.external_left.map(item => <li key={item.item}>{item.detail}</li>)}
        </ul>}

        {!preview.local && <p class="settings-inline-error">
          A factory reset can only be started from the machine swe-mux runs on. Every other
          control here is scoped to a session or a Project; this one is scoped to the machine.
        </p>}

        {!preview.relaunchable && <p class="settings-inline-error">
          This daemon has no relaunch command, so it could reset the install and never come back.
          Start swe-mux normally and try again.
        </p>}

        <label class="factory-reset-confirm">Type <code>{phrase}</code> to confirm
          <input type="text" value={typed} autocomplete="off" spellcheck={false}
            disabled={stage.name === 'resetting'}
            onInput={event => setTyped(event.currentTarget.value)} />
        </label>
      </>}

      {stage.name === 'resetting' && <p class="update-dialog-progress" aria-live="polite">
        <span class="redeploy-spinner" aria-hidden="true" /> {stage.note}
      </p>}

      {stage.name === 'error' && <p class="redeploy-interrupts redeploy-blocked">
        <strong>Not reset.</strong>
        <span>{stage.message}</span>
      </p>}

      <div class="modal-actions">
        <button type="button" onClick={onClose} disabled={!closable}>{stage.name === 'error' ? 'Close' : 'Cancel'}</button>
        <button type="button" class="primary update-dialog-reap" disabled={!armed || stage.name === 'resetting'} onClick={() => void start()}>
          {stage.name === 'resetting' ? 'Resetting…' : 'End every session and reset'}
        </button>
      </div>
    </div>
  </div>
}
