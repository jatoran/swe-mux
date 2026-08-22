import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { ModelName } from './ModelName'
import { WorkloadTelemetry } from './WorkloadTelemetry'
import {
  OPERATIONAL_TELEMETRY_PATH, type OperationalStatus,
} from './operationalTelemetry'

// What the fleet has been doing: runs and workload, explicit tool and skill activity, and
// context compaction evidence.
//
// All three were domains of Resources -> Tokens, where none of them measured a token or a
// dollar. Runs, tool calls, and compactions are descriptive behavior; the segment they sat
// in was named for money and carried the source picker, the refresh, and the cache controls
// of a completely different subject, which had to go inert on every one of these three tabs
// and say so in the status line.
//
// They belong beside Processes rather than beside spend. Processes answers what the fleet is
// running right now; this answers what it has been doing. Both are fleet-scoped, both are
// read when something looks wrong rather than when a bill is due, and neither is a currency.
//
// Money is deliberately absent. The Usage dialog is the whole cost picture, and a second
// table of one number under a second name is exactly the drift this split exists to stop.

type Domain = 'workloads' | 'tools' | 'context'

const DOMAINS: Array<[Domain, string]> = [
  ['workloads', 'runs + workload'],
  ['tools', 'tools + skills'],
  ['context', 'context + compaction'],
]

function ToolsView({ status }: { status: OperationalStatus | null }) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Only explicit provider transcript or hook records are counted. Raw names are preserved beside a versioned normalized taxonomy. Prompt similarity and file reads never imply skill usage.</p>
    <section class="usage-table"><h3>Cross-project tool metrics</h3>{status?.tools.metrics.length?<div class="usage-table-scroll"><table>
      <thead><tr><th>backend/model</th><th>project/session</th><th>tool to taxonomy</th><th>events</th><th>uses</th><th>errors</th><th>avg duration</th></tr></thead>
      <tbody>{status.tools.metrics.map(item=><tr key={`${item.session_id}-${item.raw_tool}`}>
        <td>{item.backend} · <ModelName model={item.model}/></td><td>{item.project_id?item.project_id.slice(0,8):'unassigned'} · {item.session_id.slice(0,8)}</td>
        <td>{item.raw_tool} to {item.taxonomy}</td><td>{item.events}</td><td>{item.uses}</td><td>{item.errors}</td>
        <td>{item.average_duration_ms==null?'unavailable':`${Math.round(item.average_duration_ms)}ms`}</td>
      </tr>)}</tbody>
    </table></div>:<p>No explicit tool records yet.</p>}</section>
    <section class="attribution-log"><h3>Explicit skill invocations</h3>{status?.tools.skills.length?status.tools.skills.map(item=><article key={`${item.backend}-${item.project_id}-${item.explicit_skill}`}>
      <strong>{item.explicit_skill}</strong><span>{item.backend} · {item.uses} use(s) · last {new Date(item.last_used_at*1000).toLocaleString()}</span>
    </article>):<p>No provider-native skill invocation evidence. Skills are never inferred from Markdown or generic file reads.</p>}</section>
    {/* Collapsed, and deliberately not a peer of the two tables. Parser coverage does not
        measure the fleet; it says whether the figures above were collectable at all, which
        is the question asked only once the figures already look wrong. Drawn open it read
        as a third metric and pushed the two real ones off the first screen. */}
    <details class="telemetry-collection-health">
      <summary>Collection health · parser {status?.tools.parser_version||'unknown'}{status?.tools.unknown_or_unmapped?` · ${status.tools.unknown_or_unmapped} unmapped`:''}</summary>
      <div>
        <p>{Object.entries(status?.tools.parser_versions||{}).map(([backend,version])=>`${backend}: ${version}`).join(' · ')||'provider parser versions unavailable'} · {status?.tools.unknown_or_unmapped||0} unknown or unmapped tool events.</p>
        {status?.tools.coverage.length?<div class="usage-table-scroll"><table><thead><tr><th>run</th><th>backend/parser</th><th>status</th><th>recognized</th><th>unknown</th><th>tools</th><th>skills</th><th>compactions</th></tr></thead>
          <tbody>{status.tools.coverage.map(item=><tr key={item.session_id}><td>{item.session_id.slice(0,8)}</td><td>{item.backend} · {item.parser_version}</td>
            <td>{item.status}{item.diagnostic?` · ${item.diagnostic}`:''}</td><td>{item.recognized_records}</td><td>{item.unknown_records}</td>
            <td>{item.tool_events}</td><td>{item.skill_events}</td><td>{item.compaction_events}</td>
          </tr>)}</tbody></table></div>:<p>Historical transcript reconciliation has not run yet.</p>}
      </div>
    </details>
  </div>
}

function ContextView({ status }: { status: OperationalStatus | null }) {
  return <div class="operational-telemetry">
    <p class="telemetry-caveat">Compaction counts require explicit provider-native evidence. Token drops alone remain unknown and are never counted.</p>
    <section class="attribution-log"><h3>Compaction history</h3>{status?.compactions.length?status.compactions.map(item=><article key={item.session_id}>
      <strong>{item.backend} run {item.session_id.slice(0,8)} · {item.count} compaction(s)</strong>
      <span>last {new Date(item.last_compaction_at*1000).toLocaleString()} · {item.capability}</span>
      <small>confidence {item.confidence}</small>
    </article>):<p>No explicit compaction records are available.</p>}</section>
  </div>
}

export function FleetActivityView() {
  const [domain,setDomain]=useState<Domain>('workloads')
  const [status,setStatus]=useState<OperationalStatus|null>(null)
  const [error,setError]=useState('')

  // One read for both evidence domains. `workloads` has its own endpoint and fetches
  // itself, so this stays unissued until something that needs it is selected.
  useEffect(()=>{
    if(domain==='workloads'||status)return
    let stale=false
    api<OperationalStatus>('GET',OPERATIONAL_TELEMETRY_PATH)
      .then(next=>{if(!stale)setStatus(next)})
      .catch(cause=>{if(!stale)setError(cause instanceof Error?cause.message:String(cause))})
    return()=>{stale=true}
  },[domain,status])

  return <>
    <div class="usage-domain-tabs" role="tablist" aria-label="Fleet activity">
      {DOMAINS.map(([id,label])=><button role="tab" key={id} aria-selected={domain===id} class={domain===id?'active':''} onClick={()=>setDomain(id)}>{label}</button>)}
    </div>
    <main>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      {domain==='workloads'?<WorkloadTelemetry/>
        :domain==='tools'?<ToolsView status={status}/>
        :<ContextView status={status}/>}
    </main>
    <footer><span>Durable local evidence · bounded retention · descriptive only, never a ranking</span></footer>
  </>
}
