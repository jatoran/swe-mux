import { locale as uiLocale } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { ModelName } from './ModelName'
import { OPERATIONAL_TELEMETRY_PATH, type OperationalStatus } from './operationalTelemetry'

// The legacy tool table, kept reachable beside the canonical ledger until the operator
// has read the shadow comparison and turns it off (`canonical_telemetry_legacy_dashboard_enabled`).
//
// This is deliberately its own component and its own file: the Fleet activity view must
// never read the legacy snapshot again, and a test asserts it does not. What this tab adds
// is the comparison - every place the two disagree, classified - which is the evidence the
// retirement decision is supposed to rest on. The ledger never deletes the legacy rows
// either way.

type ShadowComparison = {
  from: number; to: number; runs: number; pairs: number
  classes: Record<string, number>
  examples: Array<{ run_id: string; raw_tool: string; verdict: string; legacy: { uses: number; results: number } | null; canonical: { calls: number; with_request: number; native: number } | null }>
  interpretation: string
  legacy_dashboard_enabled: boolean
}

export function LegacyToolTelemetry({ from }: { from: number }) {
  const [status, setStatus] = useState<OperationalStatus | null>(null)
  const [shadow, setShadow] = useState<ShadowComparison | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let stale = false
    Promise.all([
      api<OperationalStatus>('GET', OPERATIONAL_TELEMETRY_PATH),
      api<ShadowComparison>('GET', `/api/telemetry/v2/shadow?from=${from}`),
    ])
      .then(([nextStatus, nextShadow]) => { if (!stale) { setStatus(nextStatus); setShadow(nextShadow) } })
      .catch(cause => { if (!stale) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [from])
  const total = shadow ? Object.values(shadow.classes).reduce((sum, value) => sum + value, 0) : 0
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">{t("LegacyToolTelemetry.2d73f478dc")}</p>
    {error && <div class="usage-error" role="alert">{error}</div>}
    <section class="usage-table"><h3 data-heading-message="LegacyToolTelemetry.bca52fd0e2">{t("LegacyToolTelemetry.bca52fd0e2")}</h3>
      {shadow ? <div>
        <p>{rich("LegacyToolTelemetry.d2cf674cfa", { value1: shadow.runs.toLocaleString(uiLocale.value), value2: shadow.pairs.toLocaleString(uiLocale.value), value3: total ? Math.round((shadow.classes.agree || 0) / total * 100) : 0 })}</p>
        <div class="usage-table-scroll"><table><thead><tr><th>{t("LegacyToolTelemetry.0889113e04")}</th><th>{t("LegacyToolTelemetry.fb09562410")}</th></tr></thead>
          <tbody>{Object.entries(shadow.classes).map(([name, count]) => <tr key={name}><td>{name.replace(/_/g, ' ')}</td><td>{count.toLocaleString(uiLocale.value)}</td></tr>)}</tbody></table></div>
        <p class="telemetry-caveat">{shadow.interpretation}</p>
        {shadow.examples.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("LegacyToolTelemetry.acba255121")}</th><th>{t("LegacyToolTelemetry.7c9bbe5ec9")}</th><th>{t("LegacyToolTelemetry.257f10c608")}</th><th>{t("LegacyToolTelemetry.569bf5f4e2")}</th><th>{t("LegacyToolTelemetry.bef32d2c31")}</th><th>{t("LegacyToolTelemetry.0889113e04")}</th></tr></thead>
          <tbody>{shadow.examples.map(item => <tr key={`${item.run_id}-${item.raw_tool}`}><td>{item.run_id.slice(0, 8)}</td><td>{item.raw_tool}</td><td>{item.legacy?.uses ?? '·'}</td><td>{item.canonical?.with_request ?? '·'}</td><td>{item.canonical?.native ?? '·'}</td><td>{item.verdict.replace(/_/g, ' ')}</td></tr>)}</tbody></table></div> : <p>{t("LegacyToolTelemetry.2179fc9205")}</p>}
      </div> : <p>{t("LegacyToolTelemetry.d753da6bf6")}</p>}
    </section>
    <section class="usage-table"><h3 data-heading-message="LegacyToolTelemetry.55bb95b2e9">{t("LegacyToolTelemetry.55bb95b2e9")}</h3>{status?.tools.metrics.length ? <div class="usage-table-scroll"><table>
      <thead><tr><th>{t("LegacyToolTelemetry.c50ed2e59e")}</th><th>{t("LegacyToolTelemetry.d68f8336c0")}</th><th>{t("LegacyToolTelemetry.2666955e8e")}</th><th>{t("LegacyToolTelemetry.862417b9e7")}</th><th>{t("LegacyToolTelemetry.f480ffb297")}</th><th>{t("LegacyToolTelemetry.be4bd56772")}</th><th>{t("LegacyToolTelemetry.04a0a6eab6")}</th></tr></thead>
      <tbody>{status.tools.metrics.map(item => <tr key={`${item.session_id}-${item.raw_tool}`}>
        <td>{item.backend} · <ModelName model={item.model}/></td><td>{item.project_id ? item.project_id.slice(0, 8) : t("LegacyToolTelemetry.2f23e841e4")} · {item.session_id.slice(0, 8)}</td>
        <td>{rich("LegacyToolTelemetry.d36e2e59f9", { raw_tool: item.raw_tool, taxonomy: item.taxonomy })}</td><td>{item.events}</td><td>{item.uses}</td><td>{item.errors}</td>
        <td>{item.average_duration_ms == null ? t("LegacyToolTelemetry.ba691ba042") : t("LegacyToolTelemetry.a6825715cf", { value1: Math.round(item.average_duration_ms) })}</td>
      </tr>)}</tbody>
    </table></div> : <p>{t("LegacyToolTelemetry.bb4c6cef3e")}</p>}<small>{rich("LegacyToolTelemetry.b65b5c9f11", { value1: status?.tools.parser_version || t("LegacyToolTelemetry.b23a6a8439"), value2: status?.tools.unknown_or_unmapped || 0 })}</small></section>
  </div>
}
