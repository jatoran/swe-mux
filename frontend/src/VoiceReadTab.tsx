import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { GrantGate } from './GrantGate'
import { SettingLink } from './SettingLink'
import { sessionDisplayName } from './sessionNames'
import type { Session, VoiceClip, VoiceContent, VoiceMode, VoiceStatus } from './types'
import {
  autoplayEnabled, beginRequestedStream, cancelRequestedStream, clipDeviceState, dismissHeldClips,
  getPlayback, heldClipTotal, newVoiceStreamId, pausePlayback, playAllHeldClips, playClip,
  playRequestedStreamFirst, seekTo, setAutoplayEnabled, subscribePlayback, unlockPlayback,
} from './voice'
import type { ClipDeviceState } from './voice'
import type { VoiceBodyVariant } from './voiceDock'

/**
 * Read aloud's operational home: the voice panel's third tab.
 *
 * Everything read aloud *does* was reachable only from three places that are each about
 * something else - a checkbox several sections into Settings, a chip on whichever pane
 * happened to be on screen, and a floating strip under that pane - so "what is speaking,
 * and what is waiting" had no surface at all. This is that surface, and it is now the only
 * read-aloud *control* surface in the workspace: the pane's player strip is gone, because a
 * per-session control drawn once per visible pane answered "what is this session doing with
 * audio" four times in a four-way split, about four different sessions, none of them
 * necessarily the one being listened to. The two things the strip could do and nothing else
 * could - scrub within a clip, and generate one on demand - are here.
 *
 * The split with Settings is the one `setting-links.md` requires:
 *
 * - **Settings -> Voice owns the master switch.** It is where `tts_enabled` is edited, and
 *   in particular the only place it can be turned *off*. This tab renders the standard
 *   gate while it is off - a gate may only ever turn something on - and, while it is on,
 *   a link back to the owner rather than a second copy of the switch.
 * - **The clip record is the join point.** One clip model, two views over it: this list is
 *   one, and the transcript's per-message markers are the other. Neither owns a clip; they
 *   read the same rows.
 * - **Which sessions participate is reported outside this tab**, by a mark on every sidebar
 *   row and workspace tab (the `voice` row field). This tab edits the mode for the session
 *   in focus, one at a time; the marks answer "which of them speak" for the whole fleet at
 *   once, which is the question a control panel following focus cannot answer.
 * - **The list is ordered by when each reply arrived**, never by when its audio finished.
 *   A held backlog is synthesized in whatever order engine slots free up, so synthesis
 *   order puts an hour-old update above the reply that just landed - which is the whole
 *   reason `source_ts` is captured at generation time (`design/features/voice.md`).
 */

/** How many clips the global list holds. Past this it is a log, not a player. */
const CLIP_LIMIT = 60

const formatClock = (seconds: number) =>
  new Date(seconds * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

/** Elapsed/total, for the transport. Clamped: a clip with no duration reads `0:00`. */
const formatSeconds = (value: number) => {
  const total = Math.max(0, Math.floor(value || 0))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/** When the clip's own message arrived, falling back to when it was synthesized. */
export const clipArrival = (clip: VoiceClip): number =>
  typeof clip.source_ts === 'number' ? clip.source_ts : clip.created_at

/**
 * What one row says about a clip: the daemon's synthesis state, or - once that is
 * settled - what this device did with it.
 *
 * The two are deliberately not one field. A clip is `ready` on the daemon forever, while
 * `played` is true on the laptop and false on the phone, so a row that reported only one
 * of them would be wrong on one of the two surfaces.
 */
export const clipRowState = (clip: VoiceClip, device: ClipDeviceState): string => {
  if (clip.status === 'synthesizing') return 'synthesizing'
  if (clip.status === 'failed') return 'failed'
  return device || 'ready'
}

export function VoiceReadTab({
  variant, status, session, mode, content, onMode, onContent, onOpenSettings, onStatusChanged,
}: {
  variant: VoiceBodyVariant
  status: VoiceStatus | null
  /** The focused agent session, or null when nothing agent-shaped has focus. */
  session: Session | null
  /** That session's resolved participation, already defaulted by App. */
  mode: VoiceMode
  /** That session's resolved content mode, already defaulted by App. */
  content: VoiceContent
  onMode: (mode: VoiceMode) => void
  onContent: (content: VoiceContent) => void
  onOpenSettings: () => void
  /** Called after the gate turns the master on, so App refetches `/api/voice`. */
  onStatusChanged: () => void
}) {
  const [clips, setClips] = useState<VoiceClip[]>([])
  const [error, setError] = useState('')
  const [speaking, setSpeaking] = useState(false)
  const [, force] = useState(0)
  const enabled = !!status?.enabled

  // Playback state is module-level and changes without a prop moving (a clip ends, a
  // backlog is held), so the rows subscribe to it directly.
  useEffect(() => subscribePlayback(() => force(value => value + 1)), [])

  const load = () => api<{ items: VoiceClip[] }>('GET', `/api/voice/clips?limit=${CLIP_LIMIT}`)
    .then(result => setClips(result.items))
    .catch(cause => setError(cause instanceof Error ? cause.message : 'clips could not be loaded'))

  // Drawn or not: the tab stays mounted so it keeps its list across tab switches, and a
  // clip that lands while the assistant is on screen must not need a refetch to appear.
  useEffect(() => {
    if (!enabled) { setClips([]); return }
    void load()
    const onClip = () => { void load() }
    window.addEventListener('mux:voice-clip', onClip)
    return () => window.removeEventListener('mux:voice-clip', onClip)
  }, [enabled])

  /**
   * Generate audio for the focused session's last reply, now.
   *
   * Gated on a focused agent and the master switch only, never on the session's own
   * mode: the daemon's manual path checks `tts_enabled` and an observable transcript
   * and nothing else, so a session set to `off` can still be asked to speak once. The
   * strip this replaced could not offer that - it was only drawn when the mode was
   * already on - which left `off` sessions reachable from the command palette alone.
   */
  const speak = async () => {
    if (!session || speaking) return
    setSpeaking(true)
    setError('')
    unlockPlayback()
    const streamId = newVoiceStreamId()
    beginRequestedStream(streamId, session.id, 'agent')
    try {
      const created = await api<VoiceClip>(
        'POST', `/api/sessions/${session.id}/voice/generate`, { stream_id: streamId })
      await load()
      if (created?.id) {
        await playRequestedStreamFirst(created.id, created.stream_id || streamId, session.id, 'agent')
      }
    } catch (cause) {
      // The stream has to be cancelled explicitly: it was opened before the request, so
      // a failed generation would otherwise leave this device waiting to play a clip
      // that will never arrive, and holding every later one behind it.
      cancelRequestedStream(streamId)
      setError(cause instanceof Error ? cause.message : 'generation failed')
    } finally { setSpeaking(false) }
  }

  if (variant === 'hidden') return <div class="voice-read hidden-variant" hidden />

  const playback = getPlayback()
  const held = heldClipTotal()

  if (variant === 'peek') {
    return <p class="voice-dock-line" title="Read aloud">
      <b>tts</b>
      <em class={enabled ? '' : 'unavailable'}>{enabled ? (autoplayEnabled() ? 'on · this device' : 'on · muted here') : 'off'}</em>
      <span>{held ? `${held} clip${held === 1 ? '' : 's'} waiting` : playback.clipId ? 'speaking' : 'nothing waiting'}</span>
    </p>
  }

  if (!enabled) {
    return <div class="voice-read">
      <GrantGate
        ids={['voice.tts']}
        heading="Read aloud is off, so nothing is generated and nothing plays."
        onGranted={onStatusChanged}
      >
        Turning it on lets agent sessions turn their completed replies into audio. Each
        session still decides whether it participates, and this device still decides
        whether it speaks - the two controls below.
      </GrantGate>
    </div>
  }

  const modeLabel = mode === 'on_demand' ? 'on demand' : mode === 'auto' ? 'auto on reply' : 'off'
  // The clip the transport addresses: whatever this device loaded last, whether or not it
  // is still playing and whichever session it belongs to. A clip older than the list's
  // window has no row to describe it, so the bar simply does not draw rather than
  // scrubbing something it cannot name.
  const loaded = clips.find(clip => clip.id === playback.clipId) || null
  // The live duration once the audio element knows it; the daemon's estimate until then,
  // so the bar's range is right on the first frame instead of snapping when metadata lands.
  const loadedDuration = playback.duration > 0 ? playback.duration : (loaded?.duration_hint_s || 0)
  return <div class="voice-read">
    <div class="voice-read-controls">
      {/* Layer 2, for the session in focus. It moved here from the pane bar: a control
          repeated on every pane answered the same question once per pane, and the pane
          it was on was whichever one happened to be drawn. */}
      <div class="voice-read-session">
        <span>session</span>
        <strong title={session ? sessionDisplayName(session) : 'No agent session focused'}>
          {session ? sessionDisplayName(session) : 'no agent focused'}
        </strong>
        <button
          class={`voice-read-mode ${mode}`}
          disabled={!session}
          aria-label={`Read aloud for this session: ${modeLabel}. Click to change.`}
          title={session
            ? `This session generates ${modeLabel} · click to cycle off → on demand → auto`
            : 'Focus an agent session to set how it participates'}
          onClick={() => onMode(mode === 'off' ? 'on_demand' : mode === 'on_demand' ? 'auto' : 'off')}
        >tts:{mode === 'on_demand' ? 'tap' : mode}</button>
        <button
          class={`voice-read-content ${content}`}
          disabled={!session}
          aria-label={`Spoken content for this session: ${content}. Click to switch.`}
          title={content === 'summary'
            ? 'Speaking LLM summaries of each reply · click for verbatim (reads the reply itself, no LLM)'
            : 'Speaking replies verbatim, no LLM involved · click for spoken summaries'}
          onClick={() => onContent(content === 'summary' ? 'verbatim' : 'summary')}
        >{content}</button>
        {/* Deliberately not disabled by `mode`: see `speak`. */}
        <button
          class="voice-read-speak"
          disabled={!session || speaking}
          aria-label="Speak the focused session’s last reply now"
          title={session
            ? `Generate ${content === 'summary' ? 'a spoken summary' : 'verbatim audio'} of this session’s last reply and play it`
            : 'Focus an agent session to speak its last reply'}
          onClick={() => void speak()}
        >{speaking ? 'speaking…' : '↻ speak'}</button>
      </div>
      {/* Layer 3, stored in this browser. */}
      <button
        class={`voice-read-autoplay ${autoplayEnabled() ? 'active' : ''}`}
        aria-pressed={autoplayEnabled()}
        title={autoplayEnabled()
          ? 'New clips play on this device, for the session you are focused on · click to stop autoplaying here'
          : 'New clips will NOT play on this device · click to autoplay them here'}
        onClick={() => setAutoplayEnabled(!autoplayEnabled())}
      >{autoplayEnabled() ? '🔊 this device' : '🔇 this device'}</button>
      {held > 0 && <button
        class="voice-read-held"
        title={`Play the ${held} clip${held === 1 ? '' : 's'} that finished while another session had focus`}
        onClick={() => { unlockPlayback(); playAllHeldClips() }}
      >▶ {held} held</button>}
      {/* Layer 1 lives in Settings and is only editable there: a gate may turn a switch
          on from anywhere, but exactly one editor may turn it off. */}
      <SettingLink target="voice.tts" class="voice-read-master" title="Read aloud is on · its master switch lives in Settings → Voice">master: on</SettingLink>
      <button class="dictation-settings" aria-label="Open Voice settings" title="Open Voice settings (engine, voice, budget, cache)" onClick={onOpenSettings}>⚙</button>
    </div>
    {error && <p class="voice-read-error">{error}</p>}
    {/* The transport, for whatever this device last loaded.
        One bar rather than a control on every row: there is exactly one audio element,
        so a per-row scrubber would be the same widget drawn N times with N-1 of them
        inert - and the row that is playing scrolls out of a sixty-clip list, taking its
        scrubber with it. It stays after a clip ends (playback keeps the clip id), which
        is what makes "play that back from the middle" possible at all. */}
    {loaded && <div class="voice-read-now" role="group" aria-label="Playback">
      <button
        class="voice-read-now-play"
        aria-label={playback.playing ? 'Pause' : 'Play'}
        title={playback.playing ? 'Pause' : 'Play'}
        onClick={() => {
          unlockPlayback()
          if (playback.playing) { pausePlayback(); return }
          void playClip(loaded.id, loaded.session_id).catch(() => setError('Playback failed.'))
        }}
      >{playback.playing ? '⏸' : '▶'}</button>
      <input
        class="voice-read-seek" type="range" min="0" max={loadedDuration || 1} step="0.1"
        value={playback.position} aria-label="Seek within the clip"
        onInput={event => seekTo(Number(event.currentTarget.value))} />
      <span class="voice-read-now-time">
        {formatSeconds(playback.position)}/{formatSeconds(loadedDuration)}
      </span>
      <span class="voice-read-now-text" title={loaded.text || ''}>{loaded.text || '…'}</span>
    </div>}
    <div class="voice-read-clips" role="list" aria-label="Voice clips">
      {clips.length === 0
        ? <p class="voice-read-empty">No clips yet. A session set to <code>auto</code> makes one at the end of every reply; <code>on demand</code> makes one when you ask.</p>
        : clips.map(clip => {
          const device = clipDeviceState(clip.id)
          const state = clipRowState(clip, device)
          const playing = playback.clipId === clip.id && playback.playing
          return <div key={clip.id} role="listitem" class={`voice-read-clip ${state}`}>
            <button
              class="voice-read-play"
              disabled={clip.status !== 'ready'}
              aria-label={playing ? 'Pause this clip' : 'Play this clip'}
              title={clip.status === 'failed' ? (clip.error || 'generation failed') : playing ? 'Pause' : 'Play'}
              onClick={() => {
                unlockPlayback()
                if (playing) { pausePlayback(); return }
                void playClip(clip.id, clip.session_id).catch(() => setError('Playback failed.'))
              }}
            >{playing ? '⏸' : '▶'}</button>
            <span class="voice-read-kind" title={`Kind: ${clip.content_mode}`}>{clip.content_mode}</span>
            <span class="voice-read-when" title={`The reply arrived at ${formatClock(clipArrival(clip))}`}>{formatClock(clipArrival(clip))}</span>
            <span class="voice-read-text" title={clip.text || clip.error || ''}>{clip.text || clip.error || '…'}</span>
            <span class={`voice-read-state ${state}`}>{state}</span>
            {device === 'held' && <button
              class="voice-read-dismiss"
              aria-label="Dismiss this session's held clips"
              title="Dismiss the clips this session is holding for you"
              onClick={() => dismissHeldClips(clip.session_id)}
            >×</button>}
          </div>
        })}
    </div>
  </div>
}
