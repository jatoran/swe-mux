import { LATENCY_STAGE_LABELS } from './voiceLatency'
import type { LatencyReportPayload, LatencyStages } from './voiceLatency'

const ms = (value: number) => `${Math.round(value)} ms`

/**
 * The measured stage breakdown for spoken commands, in Settings → Voice.
 *
 * Percentiles rather than a mean, and a separate command-only total: the roadmap's
 * exit criterion is stated for a short command, and dictation utterances are several
 * times longer to decode, so one blended average would answer neither question.
 */
export function VoiceLatencyReport({ report, onRefresh, onReset }: {
  report: LatencyReportPayload | null
  onRefresh: () => void
  onReset: () => void
}) {
  const stages = report?.stages
  return <div class="voice-latency">
    <div class="voice-latency-actions">
      <span>{report ? `${report.count} sample${report.count === 1 ? '' : 's'}` : 'unavailable'}</span>
      <button onClick={onRefresh}>Refresh</button>
      <button onClick={onReset} disabled={!report?.count} title="Discard the recorded samples and start a fresh measurement run">Reset</button>
    </div>
    {report && report.count > 0
      ? <>
        <table class="voice-latency-table">
          <thead><tr><th>stage</th><th>p50</th><th>p95</th><th>max</th></tr></thead>
          <tbody>
            {LATENCY_STAGE_LABELS.map(stage => {
              const stat = stages?.[stage.key as keyof LatencyStages]
              return <tr key={stage.key} title={stage.hint}>
                <td>{stage.label}</td>
                <td>{ms(stat?.p50 || 0)}</td>
                <td>{ms(stat?.p95 || 0)}</td>
                <td>{ms(stat?.max || 0)}</td>
              </tr>
            })}
            <tr class="voice-latency-total" title="End of speech to executed action, all utterances.">
              <td>total</td>
              <td>{ms(report.total_ms.p50)}</td>
              <td>{ms(report.total_ms.p95)}</td>
              <td>{ms(report.total_ms.max)}</td>
            </tr>
          </tbody>
        </table>
        <p class={report.command_total_ms.count && report.command_total_ms.p50 > 500 ? 'settings-inline-error' : ''}>
          {report.command_total_ms.count
            ? `Commands only (${report.command_total_ms.count}): p50 ${ms(report.command_total_ms.p50)}, p95 ${ms(report.command_total_ms.p95)}. Target is under 500 ms.`
            : 'No wake-word command has been spoken yet, so the command-only total has nothing in it.'}
        </p>
      </>
      : <p>Nothing measured yet. Turn Talk on, speak an utterance, and refresh.</p>}
  </div>
}
