import { useMemo, useState } from 'preact/hooks'
import { Dropdown } from './Dropdown.tsx'
import { SessionTopbar } from './SessionTopbar.tsx'
import { ROW_FIELD_BY_ID, SEPARATORS, SEPARATOR_IDS, type RowAlign, type RowFieldId, type RowFieldMode, type SeparatorId } from './sessionRowConfig.ts'
import { deriveRowFleetFacts } from './sessionRowFields.ts'
import { useSessionRowConfig } from './sessionRowPrefs.ts'
import {
  SESSION_TOPBAR_ACTIONS, SESSION_TOPBAR_MAX_ROWS, addSessionTopbarRow,
  defaultSessionTopbarConfig, placeSessionTopbarItem, removeSessionTopbarItem,
  removeSessionTopbarRow, sessionTopbarItemKey, setSessionTopbarMetricMode,
  unplacedSessionTopbarItems, type SessionTopbarActionId, type SessionTopbarConfig,
  type SessionTopbarItem,
} from './sessionTopbarConfig.ts'
import { loadSessionTopbarConfig, saveSessionTopbarConfig } from './sessionTopbarPrefs.ts'
import type { Session } from './types.ts'

const PREVIEW_NOW=Math.floor(Date.now()/1000)
const PREVIEW_SESSION={
  id:'topbar-preview',project_id:'preview-project',name:'ship configurable pane headers',
  backend:'preview-agent',model:'gpt-5.6-sol',state:'working',state_since:PREVIEW_NOW-420,
  turn_started_at:PREVIEW_NOW-420,created_at:PREVIEW_NOW-7200,worked_ms:51*60_000,
  context_pct:.64,context_peak_pct:.71,compaction_count:1,cost_usd:1.82,
  runtime_cwd:'D:/PROJECTS/swe-mux/frontend',spawn_cwd:'D:/PROJECTS/swe-mux',cwd:'D:/PROJECTS/swe-mux',
  project_root:'D:/PROJECTS/swe-mux',
  git:{branch:'ui-settings-updates',worktree:'ui-settings-updates',dirty:6,ahead:3,behind:0,added:284,removed:41,root:'D:/PROJECTS/swe-mux-wt/ui-settings-updates',compare_ref:'origin/master',compare_added:640,compare_removed:112,compare_files:14},
  provider_account_hashes:{openai:'preview-account'},
} as unknown as Session

const PREVIEW_MIN=300,PREVIEW_MAX=900,PREVIEW_DEFAULT=640

export function SessionTopbarSettings(){
  const [config,setConfig]=useState(loadSessionTopbarConfig)
  const [error,setError]=useState('')
  const [previewWidth,setPreviewWidth]=useState(PREVIEW_DEFAULT)
  const rowConfig=useSessionRowConfig()
  const facts=useMemo(()=>deriveRowFleetFacts([PREVIEW_SESSION],{[PREVIEW_SESSION.id]:2}),[])

  const change=(next:SessionTopbarConfig)=>{
    setConfig(next)
    void saveSessionTopbarConfig(next).then(()=>setError('')).catch(()=>setError('Could not save the top bar layout. Try again after the daemon reconnects.'))
  }
  const actionLabel=(id:SessionTopbarActionId)=>{
    if(id==='approvals')return'appr:wait'
    const action=SESSION_TOPBAR_ACTIONS.find(item=>item.id===id)
    return id==='drawer:queue'?'queue:2':action?.label.toLowerCase()||id
  }
  const itemLabel=(item:SessionTopbarItem)=>item.kind==='metric'?ROW_FIELD_BY_ID[item.id].label:SESSION_TOPBAR_ACTIONS.find(action=>action.id===item.id)?.label||item.id

  const slot=(item:SessionTopbarItem,rowIndex:number,align:RowAlign,index:number,total:number)=>{
    const other:RowAlign=align==='left'?'right':'left'
    return <li key={sessionTopbarItemKey(item)} class="topbar-slot">
      <span>{itemLabel(item)}</span>
      {item.kind==='metric'?<Dropdown ariaLabel={`${itemLabel(item)} visibility`} value={item.mode} onChange={value=>change(setSessionTopbarMetricMode(config,item.id,value as RowFieldMode))} options={[{value:'notable',label:'when notable'},{value:'always',label:'always'}]}/>:<em>shortcut</em>}
      {config.rows.length>1&&<Dropdown ariaLabel={`${itemLabel(item)} row`} value={String(rowIndex)} onChange={value=>change(placeSessionTopbarItem(config,item,Number(value),align))} options={config.rows.map((_,target)=>({value:String(target),label:`row ${target+1}`}))}/>} 
      <span class="topbar-slot-actions">
        <button type="button" title="Move earlier" disabled={index===0} onClick={()=>change(placeSessionTopbarItem(config,item,rowIndex,align,index-1))}>↑</button>
        <button type="button" title="Move later" disabled={index===total-1} onClick={()=>change(placeSessionTopbarItem(config,item,rowIndex,align,index+1))}>↓</button>
        <button type="button" title={`Move ${other}`} onClick={()=>change(placeSessionTopbarItem(config,item,rowIndex,other))}>{align==='left'?'→':'←'}</button>
        <button type="button" class="danger" title="Remove" disabled={item.kind==='metric'&&item.id==='title'} onClick={()=>change(removeSessionTopbarItem(config,item))}>×</button>
      </span>
    </li>
  }
  const side=(rowIndex:number,align:RowAlign)=>{
    const items=config.rows[rowIndex][align]
    return <div class="topbar-section-editor"><h5>{align==='left'?'Left':'Right'}</h5>{items.length?<ul>{items.map((item,index)=>slot(item,rowIndex,align,index,items.length))}</ul>:<p>Nothing placed here.</p>}</div>
  }

  const unplaced=unplacedSessionTopbarItems(config)
  return <div class="session-topbar-settings">
    <h3 data-setting="session_topbar">Session top bars</h3>
    <div class="session-topbar-preview-sticky">
      <div class="session-row-preview-heading"><div><strong>Live preview</strong><small>One active session · updates before save completes</small></div></div>
      <label class="row-size-control"><span>Preview width</span><input type="range" min={PREVIEW_MIN} max={PREVIEW_MAX} value={previewWidth} onInput={event=>setPreviewWidth(event.currentTarget.valueAsNumber)}/><output>{previewWidth}px</output></label>
      <div class="session-topbar-preview" style={{width:`${previewWidth}px`}}>
        <SessionTopbar preview session={PREVIEW_SESSION} config={config} rowConfig={rowConfig} facts={facts}
          renderAction={id=><button type="button" class={`pane-tool-label ${id==='approvals'?'approval-chip':`${id.slice('drawer:'.length)}-chip`}`}>{actionLabel(id)}</button>}
          menu={<button type="button" aria-label="More actions">⋯</button>}/>
      </div>
    </div>
    <p>Arrange session metrics and shortcuts into one to three rows. The overflow menu stays fixed so every pane keeps a recovery path even when all optional shortcuts are removed.</p>
    {error&&<p class="settings-inline-error" aria-live="polite">{error}</p>}
    <label>Row density<Dropdown value={config.density} onChange={value=>change({...config,density:value as SessionTopbarConfig['density']})} options={[{value:'compact',label:'Compact'},{value:'standard',label:'Standard'},{value:'comfortable',label:'Comfortable'}]}/></label>
    <div class="theme-actions"><button type="button" onClick={()=>change(defaultSessionTopbarConfig())}>Reset to default</button></div>

    {config.rows.map((row,rowIndex)=><section key={rowIndex} class="topbar-row-editor">
      <header><h4>Row {rowIndex+1}</h4>{config.rows.length>1&&<button type="button" onClick={()=>change(removeSessionTopbarRow(config,rowIndex))}>Remove row</button>}</header>
      <label>Separator<Dropdown value={row.separator} onChange={value=>change({...config,rows:config.rows.map((entry,index)=>index===rowIndex?{...entry,separator:value as SeparatorId}:entry)})} options={SEPARATOR_IDS.map(id=>({value:id,label:SEPARATORS[id].label}))}/></label>
      <div class="row-section-grid">{side(rowIndex,'left')}{side(rowIndex,'right')}</div>
      {!!unplaced.length&&<details class="topbar-add-items"><summary>Add metrics or shortcuts to row {rowIndex+1}</summary>
        <div><strong>Metrics</strong>{unplaced.filter(item=>item.kind==='metric').map(item=><button key={item.key} type="button" title={item.description} onClick={()=>change(placeSessionTopbarItem(config,{kind:'metric',id:item.id as RowFieldId,mode:item.id==='title'?'always':'notable'},rowIndex,'left'))}>{item.label}</button>)}</div>
        <div><strong>Shortcuts</strong>{unplaced.filter(item=>item.kind==='action').map(item=><button key={item.key} type="button" title={item.description} onClick={()=>change(placeSessionTopbarItem(config,{kind:'action',id:item.id as SessionTopbarActionId},rowIndex,'right'))}>{item.label}</button>)}</div>
      </details>}
    </section>)}
    <button type="button" class="topbar-add-row" disabled={config.rows.length>=SESSION_TOPBAR_MAX_ROWS} onClick={()=>change(addSessionTopbarRow(config))}>Add row</button>
  </div>
}
