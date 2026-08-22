import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { AutomationSpendView } from './AutomationSpendView'
import { Dropdown } from './Dropdown'
import { useModalFocus } from './modalFocus'
import { OPERATIONAL_TELEMETRY_PATH, type OperationalStatus } from './operationalTelemetry'
import { QuotaAnalytics } from './QuotaAnalytics'
import { harnesses } from './harnessRegistry'
import { UsageAgentsView } from './UsageDashboardView'
import { UsageOverview } from './UsageOverview'
import { USAGE_SEGMENTS, type UsageSegment } from './usageSegments'

// What things cost, in its own dialog rather than as a segment of Resources.
//
// It was `Resources -> Tokens`, six domain tabs deep, and the name was the least of it.
// Three of those six domains measured no tokens and no money at all - runs, tool calls, and
// compactions are behavior, and they have moved to `Resources -> Fleet activity` where the
// rest of the fleet lives. What was left is this: three pots of spend, which is a subject
// large enough to be a destination and unlike the other three meters in every way that
// matters. Processes, bandwidth, and disk are live readings of one machine that go stale in
// seconds; spend is a retrospective question asked of a ledger, and the two get asked at
// different times for different reasons.
//
// The nesting is the other half. Tokens was reached at modal -> segment -> domain -> view,
// with the historical view's own controls sitting in a shared actions row that had to go
// inert on five of six tabs and print "filters below apply only to this telemetry category"
// to explain itself. Every control now belongs to the one segment it applies to, and the
// depth is modal -> segment -> view with the third level surviving only on Agents, which is
// the one place a range and an interval and a metric are all genuinely orthogonal.
//
// `usage.open` finally opens this. It has been named "Open usage analytics" the whole time
// while landing on a segment of a dialog about processes and disk.

type Props = {
  /** Which segment to open on. A caller that named one has already said what it wants. */
  initial?: UsageSegment
  onClose: () => void
  /** The collector and its retention live in Settings. */
  onConfigure: () => void
  /**
   * The other reading of the Automation segment's table, where the rules that produced it
   * are editable. Optional so a harness can mount the dialog without the whole app behind
   * it; the link is simply absent when nothing can answer it.
   */
  onOpenAutomation?: () => void
}

export function UsageModal({ initial = 'overview', onClose, onConfigure, onOpenAutomation }: Props) {
  const [segment, setSegment] = useState<UsageSegment>(initial)
  const [quotaProvider, setQuotaProvider] = useState<'all' | string>('all')
  const [operational, setOperational] = useState<OperationalStatus | null>(null)
  const [quotaError, setQuotaError] = useState('')
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel, onClose)

  const active = USAGE_SEGMENTS.find(item => item.id === segment) || USAGE_SEGMENTS[0]
  const quotaProviders = harnesses().filter(item => item.capabilities.provider_accounts).map(item => item.name)

  // Correlated activity is the only thing Quota needs from the operational store, and it is
  // the only segment that needs it, so the read waits until that segment is entered and is
  // not re-issued when it is left and re-entered.
  useEffect(() => {
    if (segment !== 'quota' || operational) return
    let stale = false
    api<OperationalStatus>('GET', OPERATIONAL_TELEMETRY_PATH)
      .then(next => { if (!stale) setOperational(next) })
      .catch(cause => { if (!stale) setQuotaError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [segment, operational])

  return <div
    class={`usage-layer resources-layer usage-dialog usage-${segment}`}
    role="dialog"
    aria-modal="true"
    aria-label="Usage"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}
  >
    <section class="usage-panel resources-panel" ref={panel}>
      <header>
        <div><span>{active.heading}</span><strong>{active.title}</strong></div>
        <div class="usage-header-actions">
          <button onClick={onConfigure}>configure</button>
          <button aria-label="Close usage" onClick={onClose}>×</button>
        </div>
      </header>
      <div class="segmented-tabs resources-segmented usage-segmented" role="tablist" aria-label="Usage">
        {USAGE_SEGMENTS.map(item => <button
          key={item.id}
          role="tab"
          aria-selected={item.id === segment}
          class={item.id === segment ? 'active' : ''}
          title={item.title}
          onClick={() => setSegment(item.id)}
        >{item.label}</button>)}
      </div>
      {/* Unmounted when not selected, for the same reason the Resources segments are: each
          of these four reaches a different set of endpoints, and a dialog that quietly held
          all four mounted would issue every one of those reads to show one of them. */}
      {segment === 'overview' && <UsageOverview onOpen={setSegment} />}
      {segment === 'agents' && <UsageAgentsView onConfigure={onConfigure} />}
      {segment === 'automation' && <>
        {onOpenAutomation && <div class="usage-actions automation-elsewhere">
          {/* This segment says what automation costs; it cannot turn any of it off. The
              rule that produced a row is editable in one place, and a reader who has just
              found an expensive row is on their way there. */}
          <span>Turn a rule off</span>
          <div><button onClick={onOpenAutomation}>Automation dashboard</button></div>
        </div>}
        <main><AutomationSpendView /></main>
      </>}
      {segment === 'quota' && <>
        <div class="usage-actions">
          <label>provider<Dropdown value={quotaProvider} onChange={setQuotaProvider} options={[
            { value: 'all', label: 'all providers' },
            ...quotaProviders.map(provider => ({ value: provider, label: provider })),
          ]} /></label>
        </div>
        <main>
          {quotaError && <div class="usage-error" role="alert">{quotaError}</div>}
          <QuotaAnalytics provider={quotaProvider} attribution={operational?.quota.attributions || []} />
        </main>
      </>}
      <footer><span>{active.footer}</span></footer>
    </section>
  </div>
}
