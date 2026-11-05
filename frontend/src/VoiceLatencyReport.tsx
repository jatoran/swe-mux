import { t } from './localization/index.ts'
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
      <span>{report ? t("VoiceLatencyReport.84ba09ab1c", { count: report.count, value2: (report.count === 1) ? 'yes' : 'no' }) : t("VoiceLatencyReport.ba691ba042")}</span>
      <button onClick={onRefresh}>{t("VoiceLatencyReport.0e91610117")}</button>
      <button onClick={onReset} disabled={!report?.count} title={t("VoiceLatencyReport.7ecc3a849c")}>{t("VoiceLatencyReport.daee7606b3")}</button>
    </div>
    {report && report.count > 0 ? <>
        <table class="voice-latency-table">
          <thead><tr><th>{t("VoiceLatencyReport.c7ff6dcd94")}</th><th>{t("VoiceLatencyReport.875636a511")}</th><th>{t("VoiceLatencyReport.f771f4d32e")}</th><th>{t("VoiceLatencyReport.9baf3a4031")}</th></tr></thead>
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
            <tr class="voice-latency-total" title={t("VoiceLatencyReport.d35a3b9ff1")}>
              <td>{t("VoiceLatencyReport.11239872d1")}</td>
              <td>{ms(report.total_ms.p50)}</td>
              <td>{ms(report.total_ms.p95)}</td>
              <td>{ms(report.total_ms.max)}</td>
            </tr>
          </tbody>
        </table>
        <p class={report.command_total_ms.count && report.command_total_ms.p50 > 500 ? 'settings-inline-error' : ''}>
          {report.command_total_ms.count ? t("VoiceLatencyReport.0d695533fd", { count: report.command_total_ms.count, value2: ms(report.command_total_ms.p50), value3: ms(report.command_total_ms.p95) }) : t("VoiceLatencyReport.88d118bab0")}
        </p>
      </> : <p>{t("VoiceLatencyReport.5f247dafd3")}</p>}
  </div>
}
