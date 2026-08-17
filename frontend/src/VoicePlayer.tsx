import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { Session, VoiceClip, VoiceContent, VoiceStatus } from './types'
import {
  autoplayEnabled, beginRequestedStream, cancelRequestedStream, getPlayback,
  newVoiceStreamId, pausePlayback, playClip, playRequestedStreamFirst, seekTo,
  setAutoplayEnabled, subscribePlayback, unlockPlayback,
} from './voice'
import { VoiceCommandsButton } from './VoiceCommandsButton'
import type { Command } from './commands'
import { sessionDisplayName } from './sessionNames'

const formatSeconds = (value: number) => {
  const total = Math.max(0, Math.floor(value || 0))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

// One compact strip per agent pane: latest clips, seek, on-demand generation, the
// verbatim/summary switch, and the per-device autoplay switch. Everything else
// (mode, engine, voices) lives in the pane chip, context menu, and Settings so the
// strip stays one row.
export function VoicePlayer({ session, status, mode, commands, onSession, onOpenSettings }: { session: Session; status: VoiceStatus; mode: 'on_demand' | 'auto'; commands:Command[]; onSession: (session: Session) => void; onOpenSettings: () => void }) {
  const [clips, setClips] = useState<VoiceClip[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [, force] = useState(0)
  useEffect(() => subscribePlayback(() => force(value => value + 1)), [])

  const load = () => api<{ items: VoiceClip[] }>('GET', `/api/voice/clips?session=${encodeURIComponent(session.id)}&limit=20`)
    .then(result => {
      setClips(result.items)
      setSelectedId(current => current && result.items.some(clip => clip.id === current) ? current : (result.items[0]?.id || null))
      return result.items
    })
  useEffect(() => { void load().catch(() => {}) }, [session.id])
  useEffect(() => {
    const onClip = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId: string; clipId: string; status: string }>).detail
      if (detail.sessionId !== session.id) return
      void load().then(items => {
        if (detail.status === 'ready' && items.some(clip => clip.id === detail.clipId)) setSelectedId(detail.clipId)
      }).catch(() => {})
    }
    window.addEventListener('mux:voice-clip', onClip)
    return () => window.removeEventListener('mux:voice-clip', onClip)
  }, [session.id])

  const index = Math.max(0, clips.findIndex(clip => clip.id === selectedId))
  const clip = clips[index] || null
  const playback = getPlayback()
  const isCurrent = !!clip && playback.clipId === clip.id
  const duration = isCurrent && playback.duration > 0 ? playback.duration : (clip?.duration_hint_s || 0)
  const position = isCurrent ? playback.position : 0

  const togglePlay = () => {
    if (!clip || clip.status !== 'ready') return
    unlockPlayback()
    if (isCurrent && playback.playing) pausePlayback()
    else void playClip(clip.id, session.id).catch(() => setError('Playback failed.'))
  }
  const generate = async () => {
    if (busy) return
    setBusy(true); setError(''); unlockPlayback()
    const streamId=newVoiceStreamId()
    beginRequestedStream(streamId,session.id,'agent')
    try {
      const created = await api<VoiceClip>('POST', `/api/sessions/${session.id}/voice/generate`,{stream_id:streamId})
      await load()
      if (created?.id) { setSelectedId(created.id); await playRequestedStreamFirst(created.id,created.stream_id||streamId,session.id,'agent') }
    } catch (cause) {
      cancelRequestedStream(streamId)
      setError(cause instanceof Error ? cause.message : 'generation failed')
    } finally { setBusy(false) }
  }
  const content: VoiceContent = session.voice_content === 'summary' || session.voice_content === 'verbatim' ? session.voice_content : status.content
  const toggleContent = () => {
    const next: VoiceContent = content === 'summary' ? 'verbatim' : 'summary'
    void api<Session>('PATCH', `/api/sessions/${session.id}`, { voice_content: next })
      .then(onSession)
      .catch(cause => setError(cause instanceof Error ? cause.message : 'could not switch content'))
  }
  const clipTitle = clip
    ? `${clip.content_mode} · ${clip.voice} · ${new Date(clip.created_at * 1000).toLocaleTimeString()}${clip.model ? ` · ${clip.model}` : ''}`
    : 'No clips yet'

  return <div class="voice-strip" role="group" aria-label={`Read aloud for ${sessionDisplayName(session)}`}>
    <button class="voice-play" disabled={!clip || clip.status !== 'ready'} onClick={togglePlay}
      title={clip ? (isCurrent && playback.playing ? 'Pause' : 'Play clip') : 'No clip yet'}
      aria-label={isCurrent && playback.playing ? 'Pause clip' : 'Play clip'}>
      {isCurrent && playback.playing ? '⏸' : '▶'}
    </button>
    <input class="voice-seek" type="range" min="0" max={duration || 1} step="0.1" value={position}
      disabled={!isCurrent} aria-label="Seek within clip"
      onInput={event => seekTo(Number(event.currentTarget.value))} />
    <span class="voice-time">{formatSeconds(position)}/{formatSeconds(duration)}</span>
    <span class="voice-nav" title={clipTitle}>
      <button disabled={index >= clips.length - 1} aria-label="Older clip" onClick={() => setSelectedId(clips[index + 1]?.id || null)}>‹</button>
      <span>{clips.length ? `${index + 1}/${clips.length}` : '0/0'}</span>
      <button disabled={index <= 0} aria-label="Newer clip" onClick={() => setSelectedId(clips[index - 1]?.id || null)}>›</button>
    </span>
    <button class="voice-generate" disabled={busy} onClick={() => void generate()}
      title={`Generate ${content === 'summary' ? 'a spoken summary' : 'verbatim audio'} of the last reply now`}>
      {busy ? 'speaking…' : '↻ speak'}
    </button>
    <button class={`voice-content ${content}`} aria-label={`Spoken content for this session: ${content}. Click to switch.`}
      title={content === 'summary'
        ? 'Speaking LLM summaries of each reply · click for verbatim (reads the reply itself, no LLM)'
        : 'Speaking replies verbatim, no LLM involved · click for spoken summaries'}
      onClick={toggleContent}>
      {content}
    </button>
    <button class={`voice-autoplay ${autoplayEnabled() ? 'active' : ''}`} aria-pressed={autoplayEnabled()}
      title={autoplayEnabled()
        ? 'New clips play automatically on this device (browser) · click to stop autoplaying here'
        : 'New clips will NOT play on this device (browser) · click to autoplay them here'}
      onClick={() => setAutoplayEnabled(!autoplayEnabled())}>
      {autoplayEnabled() ? '🔊 this device' : '🔇 this device'}
    </button>
    {mode === 'auto' && <span class="voice-flag" title="Every completed reply generates audio automatically">auto</span>}
    {clip?.status === 'failed' && <span class="voice-error" title={clip.error || 'generation failed'}>failed</span>}
    {error && <span class="voice-error" title={error}>{error.length > 42 ? `${error.slice(0, 42)}…` : error}</span>}
    <VoiceCommandsButton commands={commands} configuredCommands={status.commands} compact/>
    {/* Trailing, so the controls the strip exists for keep the leading slots. It is the
        route out of the strip into everything the strip cannot hold: engine, voice, model,
        content default, budget, cache. */}
    <button class="voice-settings-open" aria-label="Open Voice settings" title="Open Voice settings (engine, voice, budget, cache)" onClick={onOpenSettings}>⚙</button>
  </div>
}
