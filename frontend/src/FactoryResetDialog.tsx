import { rich, t } from './localization/index.ts'
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
    setStage({ name: 'resetting', get note() { return t("FactoryResetDialog.988ab6fd25") } })
    try {
      const accepted = await requestFactoryReset(typed, external)
      if (accepted.clear_client_storage) {
        setStage({ name: 'resetting', get note() { return t("FactoryResetDialog.928f2c0ff7") } })
        await clearClientStorage()
      }
      setStage({ name: 'resetting', get note() { return t("FactoryResetDialog.b4f43c296a") } })
      if (await waitForDaemon()) { location.reload(); return }
      setStage({
        name: 'error',
        message: t("FactoryResetDialog.488b2737cb")
          + t("FactoryResetDialog.62e40edc64"),
      })
    } catch (cause) {
      setStage({ name: 'error', message: cause instanceof Error ? cause.message : String(cause) })
    }
  }, [external, preview, typed])

  const phrase = preview?.confirmation_phrase || t("FactoryResetDialog.71718398e5")
  const armed = !!preview && preview.relaunchable && preview.local
    && confirmationMatches(typed, phrase)
  const sessions = preview?.sessions || []

  return <div class="modal-layer daemon-reload-layer" role="alertdialog" aria-modal="true" aria-label={t("FactoryResetDialog.46a0b85a51")} onClick={() => { if (closable) onClose() }}>
    <div class="modal daemon-reload-modal factory-reset-dialog" ref={ref} onClick={event => event.stopPropagation()}>
      <h2 data-heading-message="FactoryResetDialog.593038df97">{t("FactoryResetDialog.593038df97")}</h2>

      {stage.name === 'loading' && <p>{t("FactoryResetDialog.90369d250a")}</p>}

      {preview && <>
        <p class={`redeploy-interrupts${sessions.length ? ' redeploy-blocked' : ''}`}>
          <strong>{sessions.length ? t("FactoryResetDialog.8db15a168e", { length: sessions.length }) : t("FactoryResetDialog.e2e8c68312")}</strong>
          <span>{sessions.length ? t("FactoryResetDialog.acbd90cd96") : t("FactoryResetDialog.6abec3acd7")}</span>
        </p>
        {sessions.length > 0 && <ul class="factory-reset-sessions">
          {sessions.map(session => <li key={session.id}>
            <strong>{session.name || session.id}</strong>
            <small>{[session.project, session.backend, session.state].filter(Boolean).join(' · ')}</small>
          </li>)}
        </ul>}

        <p>{rich("FactoryResetDialog.a9d80eb7c0", { value1: <code>{preview.data_dir}</code>, value2: <code>{t("FactoryResetDialog.e46d8b606c")}</code>, length: preview.entries.length })}</p>

        <p class="profile-hint">{rich("FactoryResetDialog.68fb98d1de", { value1: <strong>{t("FactoryResetDialog.2ba578348b")}</strong>, value2: <code>{t("FactoryResetDialog.d26d49f68e")}</code>, value3: preview.worktrees.length > 0 ? t("FactoryResetDialog.e74249d81e", { length: preview.worktrees.length, value2: (preview.worktrees.length === 1) ? 'yes' : 'no' }) : t("FactoryResetDialog.e2ebe8ea07") })}</p>

        <label class="check" data-setting="factory_reset_external">
          <span>{t("FactoryResetDialog.59b618019f")}</span>
          <input type="checkbox" checked={external} disabled={stage.name === 'resetting'} onChange={event => setExternal(event.currentTarget.checked)} />
          <small>{rich("FactoryResetDialog.55a4710838", { value1: <code>{t("FactoryResetDialog.37e4f76126")}</code>, value2: <code>{t("FactoryResetDialog.23b532f481")}</code> })}</small>
        </label>

        {external && preview.external_left.length > 0 && <ul class="factory-reset-left">
          {preview.external_left.map(item => <li key={item.item}>{item.detail}</li>)}
        </ul>}

        {!preview.local && <p class="settings-inline-error">{t("FactoryResetDialog.80a3640b1d")}</p>}

        {!preview.relaunchable && <p class="settings-inline-error">{t("FactoryResetDialog.5f2da3f44f")}</p>}

        <label class="factory-reset-confirm">{rich("FactoryResetDialog.99597a7c29", { value1: <code>{phrase}</code>, value2: <input type="text" value={typed} autocomplete="off" spellcheck={false}
            disabled={stage.name === 'resetting'}
            onInput={event => setTyped(event.currentTarget.value)} /> })}</label>
      </>}

      {stage.name === 'resetting' && <p class="update-dialog-progress" aria-live="polite">
        <span class="redeploy-spinner" aria-hidden="true" /> {stage.note}
      </p>}

      {stage.name === 'error' && <p class="redeploy-interrupts redeploy-blocked">
        <strong>{t("FactoryResetDialog.baa4363bff")}</strong>
        <span>{stage.message}</span>
      </p>}

      <div class="modal-actions">
        <button type="button" onClick={onClose} disabled={!closable}>{stage.name === 'error' ? t("FactoryResetDialog.7d9eb7acb1") : t("FactoryResetDialog.19766ed6cc")}</button>
        <button type="button" class="primary update-dialog-reap" disabled={!armed || stage.name === 'resetting'} onClick={() => void start()}>
          {stage.name === 'resetting' ? t("FactoryResetDialog.1505f40de6") : t("FactoryResetDialog.fd2e336163")}
        </button>
      </div>
    </div>
  </div>
}
