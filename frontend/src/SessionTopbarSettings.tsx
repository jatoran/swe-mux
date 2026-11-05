import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useMemo, useRef, useState } from 'preact/hooks'
import { currentProfile, type SettingsProfile } from './deviceSettings.ts'
import { Dropdown } from './Dropdown.tsx'
import { SessionTopbar } from './SessionTopbar.tsx'
import {
  ROW_FIELD_BY_ID, SEPARATORS, SEPARATOR_IDS, type RowAlign, type RowFieldId,
  type RowFieldMode, type SeparatorId,
} from './sessionRowConfig.ts'
import { deriveRowFleetFacts } from './sessionRowFields.ts'
import { useSessionRowConfig } from './sessionRowPrefs.ts'
import { CWD_STYLE_OPTIONS } from './SessionRowSettings.tsx'
import {
  SESSION_TOPBAR_ACTIONS, SESSION_TOPBAR_MAX_ROWS, addSessionTopbarRow,
  defaultSessionTopbarConfig, placeSessionTopbarItem, removeSessionTopbarItem,
  removeSessionTopbarRow, sessionTopbarItemKey, sessionTopbarMetricHasStyle,
  sessionTopbarMetricStyle, setSessionTopbarMetricMode, setSessionTopbarMetricStyle,
  unplacedSessionTopbarItems, type SessionTopbarActionId, type SessionTopbarConfig,
  type SessionTopbarItem, type SessionTopbarMetricStyle,
} from './sessionTopbarConfig.ts'
import { hasMobileSessionTopbarConfig, inheritDesktopSessionTopbarConfig, saveSessionTopbarConfig, useSessionTopbarConfig } from './sessionTopbarPrefs.ts'
import type { Session } from './types.ts'

const PREVIEW_NOW=Math.floor(Date.now()/1000)
const PREVIEW_SESSION={
  id:'topbar-preview',project_id:'preview-project',get name() { return t("SessionTopbarSettings.ff72e69d00") },
  // A registered harness, not a placeholder name: the context gauge is gated on
  // the harness's `measurement` capability, so a made-up backend previewed a
  // top bar whose placed context drew nothing while the real one drew fine.
  backend:'codex',model:'gpt-5.6-sol',state:'working',state_since:PREVIEW_NOW-420,
  turn_started_at:PREVIEW_NOW-420,created_at:PREVIEW_NOW-7200,worked_ms:51*60_000,
  context_pct:.64,context_peak_pct:.71,compaction_count:1,cost_usd:1.82,
  // Two levels below the root, so the folder-name and path-inside renderings
  // of the working directory preview as different things.
  runtime_cwd:'D:/PROJECTS/swe-mux/frontend/src',spawn_cwd:'D:/PROJECTS/swe-mux',cwd:'D:/PROJECTS/swe-mux',
  project_root:'D:/PROJECTS/swe-mux',
  git:{branch:'ui-settings-updates',worktree:'ui-settings-updates',dirty:6,ahead:3,behind:0,added:284,removed:41,root:'D:/PROJECTS/swe-mux-wt/ui-settings-updates',compare_ref:'origin/master',compare_added:640,compare_removed:112,compare_files:14},
  provider_account_hashes:{openai:'preview-account'},
  harness_status:{
    effort:'xhigh',permission_mode:'acceptEdits',
    rate_limits:{five_hour:{used_pct:37,resets_at:PREVIEW_NOW+3*3600},seven_day:{used_pct:62,resets_at:PREVIEW_NOW+2*86400}},
  },
} as unknown as Session

const METRIC_STYLE_OPTIONS:Partial<Record<RowFieldId,Array<{value:SessionTopbarMetricStyle;label:string}>>>={
  context:[
    {value:'percent',get label() { return t("SessionTopbarSettings.8975c9b4ea") }},
    {value:'gauge',get label() { return t("SessionTopbarSettings.a90fd9a9a1") }},
    {value:'both',get label() { return t("SessionTopbarSettings.ee6516080a") }},
  ],
  cwd:CWD_STYLE_OPTIONS,
}

export function SessionTopbarSettings(){
  const [profile,setProfile]=useState(currentProfile)
  return <div class="session-topbar-settings">
    <h3 data-setting="session_topbar" data-heading-message="heading.363df1ed9e">{t("heading.363df1ed9e")}</h3>
    <div class="settings-profile-switch" role="group" aria-label={t("SessionTopbarSettings.08f2b3f6f4")}>
      {(['desktop','mobile'] as const).map(id=><button type="button" key={id} aria-pressed={profile===id} class={profile===id?'is-active':''} onClick={()=>setProfile(id)}>{id==='desktop' ? t("SessionTopbarSettings.9bd88f2485") : t("SessionTopbarSettings.cbaad3cf4a")}</button>)}
    </div>
    <SessionTopbarEditor key={profile} profile={profile}/>
  </div>
}

function SessionTopbarEditor({profile}:{profile:SettingsProfile}){
  const config=useSessionTopbarConfig(profile)
  const [error,setError]=useState('')
  const request=useRef(0)
  const rowConfig=useSessionRowConfig()
  const facts=useMemo(()=>deriveRowFleetFacts([PREVIEW_SESSION],{[PREVIEW_SESSION.id]:2}), [uiLocale.value, catalogRevision.value])

  const save=(write:Promise<void>)=>{
    const id=++request.current
    setError('')
    void write.then(()=>{if(id===request.current)setError('')}).catch(()=>{
      if(id===request.current)setError(t("SessionTopbarSettings.e9c803e6a6"))
    })
  }
  const change=(next:SessionTopbarConfig)=>save(saveSessionTopbarConfig(next,profile))
  const actionLabel=(id:SessionTopbarActionId)=>{
    if(id==='approvals')return t("SessionTopbarSettings.22bed2c123")
    const action=SESSION_TOPBAR_ACTIONS.find(item=>item.id===id)
    return id==='drawer:queue'?t("SessionTopbarSettings.3b67bfa6cb"):action?.label.toLowerCase()||id
  }
  const itemLabel=(item:SessionTopbarItem)=>item.kind==='metric'?ROW_FIELD_BY_ID[item.id].label:SESSION_TOPBAR_ACTIONS.find(action=>action.id===item.id)?.label||item.id

  const slot=(item:SessionTopbarItem,rowIndex:number,align:RowAlign,index:number,total:number)=>{
    const other:RowAlign=align==='left'?'right':'left'
    return <li key={sessionTopbarItemKey(item)} class="topbar-slot">
      <span>{itemLabel(item)}</span>
      {item.kind==='metric' ? <Dropdown ariaLabel={t("SessionTopbarSettings.e1025b7b12", { value1: itemLabel(item) })} value={item.mode} onChange={value=>change(setSessionTopbarMetricMode(config,item.id,value as RowFieldMode))} options={[{value:'notable',get label() { return t("SessionTopbarSettings.ea44c278ae") }},{value:'always',get label() { return t("SessionTopbarSettings.9cdc6c47aa") }}]}/> : <em>{t("SessionTopbarSettings.8dfea68567")}</em>}
      {item.kind==='metric'&&sessionTopbarMetricHasStyle(item.id) && <Dropdown ariaLabel={t("SessionTopbarSettings.ddf8975f92", { value1: itemLabel(item) })} value={sessionTopbarMetricStyle(item,rowConfig)??''} onChange={value=>change(setSessionTopbarMetricStyle(config,item.id,value as SessionTopbarMetricStyle))} options={METRIC_STYLE_OPTIONS[item.id]??[]}/>}
      {config.rows.length>1 && <Dropdown ariaLabel={t("SessionTopbarSettings.a69d73aacd", { value1: itemLabel(item) })} value={String(rowIndex)} onChange={value=>change(placeSessionTopbarItem(config,item,Number(value),align))} options={config.rows.map((_,target)=>({value:String(target),get label() { return t("SessionTopbarSettings.aa4fa12b36", { value1: target+1 }) }}))}/>}
      <span class="topbar-slot-actions">
        <button type="button" title={t("SessionTopbarSettings.736612d4d0")} disabled={index===0} onClick={()=>change(placeSessionTopbarItem(config,item,rowIndex,align,index-1))}>↑</button>
        <button type="button" title={t("SessionTopbarSettings.d6e856081e")} disabled={index===total-1} onClick={()=>change(placeSessionTopbarItem(config,item,rowIndex,align,index+1))}>↓</button>
        <button type="button" title={t("SessionTopbarSettings.5c80dbc9f4", { other: other })} onClick={()=>change(placeSessionTopbarItem(config,item,rowIndex,other))}>{align==='left' ? '→' : '←'}</button>
        <button type="button" class="danger" title={t("SessionTopbarSettings.c3812fc4ac")} onClick={()=>change(removeSessionTopbarItem(config,item))}>×</button>
      </span>
    </li>
  }
  const side=(rowIndex:number,align:RowAlign)=>{
    const items=config.rows[rowIndex][align]
    return <div class="topbar-section-editor"><h5>{align==='left' ? t("SessionTopbarSettings.58eb9032e3") : t("SessionTopbarSettings.883361d5d6")}</h5>{items.length ? <ul>{items.map((item,index)=>slot(item,rowIndex,align,index,items.length))}</ul> : <p>{t("SessionTopbarSettings.9124e73399")}</p>}</div>
  }

  const unplaced=unplacedSessionTopbarItems(config)
  const inherited=profile==='mobile'&&!hasMobileSessionTopbarConfig()
  return <>
    <div class="session-topbar-preview-sticky">
      <div class="session-row-preview-heading"><div><strong>{t("SessionTopbarSettings.acb4308ef0", { value1: (profile==='desktop') ? 'yes' : 'no' })}</strong><small>{t("SessionTopbarSettings.0907e2aab3")}</small></div></div>
      <div class="session-topbar-preview">
        <SessionTopbar preview session={PREVIEW_SESSION} config={config} rowConfig={rowConfig} facts={facts}
          renderAction={id=><button type="button" class={`pane-tool-label ${id==='approvals'?'approval-chip':`${id.slice('drawer:'.length)}-chip`}`}>{actionLabel(id)}</button>}
          menu={<button type="button" aria-label={t("SessionTopbarSettings.f8d46c2570")}>⋯</button>}/>
      </div>
    </div>
    <p class="topbar-profile-status">{profile==='desktop' ? t("SessionTopbarSettings.25c42b37bf") : inherited ? t("SessionTopbarSettings.3df2ddd005") : t("SessionTopbarSettings.acf4fc6cdc")}</p>
    <p>{t("SessionTopbarSettings.d797dc8e48")}</p>
    {error && <p class="settings-inline-error" aria-live="polite">{error}</p>}
    <label>{rich("SessionTopbarSettings.e6d966b939", { value1: <Dropdown value={config.density} onChange={value=>change({...config,density:value as SessionTopbarConfig['density']})} options={[{value:'compact',get label() { return t("SessionTopbarSettings.99452646e3") }},{value:'standard',get label() { return t("SessionTopbarSettings.ef6691545d") }},{value:'comfortable',get label() { return t("SessionTopbarSettings.459a23a598") }}]}/> })}</label>
    <div class="theme-actions"><button type="button" onClick={()=>change(defaultSessionTopbarConfig())}>{t("SessionTopbarSettings.bc5b45ae7b")}</button>{profile==='mobile'&&!inherited && <button type="button" onClick={()=>save(inheritDesktopSessionTopbarConfig())}>{t("SessionTopbarSettings.7daabef0da")}</button>}</div>

    {config.rows.map((row,rowIndex)=><section key={rowIndex} class="topbar-row-editor">
      <header><h4>{rich("SessionTopbarSettings.b78831f446", { value1: rowIndex+1 })}</h4>{config.rows.length>1 && <button type="button" onClick={()=>change(removeSessionTopbarRow(config,rowIndex))}>{t("SessionTopbarSettings.1810fddd9e")}</button>}</header>
      <label>{rich("SessionTopbarSettings.7cfd5305e0", { value1: <Dropdown value={row.separator} onChange={value=>change({...config,rows:config.rows.map((entry,index)=>index===rowIndex?{...entry,separator:value as SeparatorId}:entry)})} options={SEPARATOR_IDS.map(id=>({value:id,label:SEPARATORS[id].label}))}/> })}</label>
      <div class="row-section-grid">{side(rowIndex,'left')}{side(rowIndex,'right')}</div>
      {!!unplaced.length && <details class="topbar-add-items"><summary>{rich("SessionTopbarSettings.7ddf802851", { value1: rowIndex+1 })}</summary>
        <div><strong>{t("SessionTopbarSettings.a58da793c7")}</strong>{unplaced.filter(item=>item.kind==='metric').map(item=><button key={item.key} type="button" title={item.description} onClick={()=>change(placeSessionTopbarItem(config,{kind:'metric',id:item.id as RowFieldId,mode:item.id==='title'?'always':'notable'},rowIndex,'left'))}>{item.label}</button>)}</div>
        <div><strong>{t("SessionTopbarSettings.46e978d0c1")}</strong>{unplaced.filter(item=>item.kind==='action').map(item=><button key={item.key} type="button" title={item.description} onClick={()=>change(placeSessionTopbarItem(config,{kind:'action',id:item.id as SessionTopbarActionId},rowIndex,'right'))}>{item.label}</button>)}</div>
      </details>}
    </section>)}
    <button type="button" class="topbar-add-row" disabled={config.rows.length>=SESSION_TOPBAR_MAX_ROWS} onClick={()=>change(addSessionTopbarRow(config))}>{t("SessionTopbarSettings.1868a8dd78")}</button>
  </>
}
