/** Browser-owned layout model for configurable session pane top bars. */

import { DRAWER_TABS, type DrawerTabId } from './drawerTabs.ts'
import {
  CONTEXT_ROW_RENDERS, CWD_STYLES, ROW_FIELDS, SEPARATORS, type ContextRowRender, type CwdStyle,
  type RowAlign, type RowFieldId, type RowFieldMode, type SeparatorId, type SessionRowConfig,
} from './sessionRowConfig.ts'

/**
 * Version 2 made the title removable.
 *
 * Under version 1 the editor could not remove the title, so a stored layout without one
 * could only be malformed, and normalization put it back at the head of the first row.
 * A version-2 layout without a title is a choice, and is kept. The stored `version` is
 * therefore what distinguishes the two: a blob carrying `1`, or none at all, still gets
 * the repair, and every write from this build stamps `2`.
 */
export const SESSION_TOPBAR_VERSION = 2
const SESSION_TOPBAR_TITLE_REMOVABLE_VERSION = 2
export const SESSION_TOPBAR_MAX_ROWS = 3

export type SessionTopbarDensity = 'compact' | 'standard' | 'comfortable'
export type SessionTopbarActionId = 'approvals' | `drawer:${DrawerTabId}`
/** The per-item renderings a top-bar metric may carry, by field. */
export type SessionTopbarMetricStyle = ContextRowRender | CwdStyle
/**
 * A placed metric. `style` exists for the fields whose rendering the sidebar
 * decides with a bar-wide setting the top bar may want to differ from: the
 * sidebar draws context on its indicator by default, and a top bar has no
 * indicator, so a placed `context` inherited `arc` and drew nothing; and the
 * working directory reads well as a folder name in a narrow sidebar row and as
 * a path in a wide pane header. Absent, the item follows the sidebar.
 */
export type SessionTopbarMetricItem = { kind:'metric';id:RowFieldId;mode:RowFieldMode;style?:SessionTopbarMetricStyle }
export type SessionTopbarActionItem = { kind:'action';id:SessionTopbarActionId }
export type SessionTopbarItem = SessionTopbarMetricItem | SessionTopbarActionItem
export type SessionTopbarRow = { left:SessionTopbarItem[];right:SessionTopbarItem[];separator:SeparatorId }
export type SessionTopbarConfig = {
  version:number
  density:SessionTopbarDensity
  rows:SessionTopbarRow[]
}

export type SessionTopbarCatalogItem = {
  key:string
  kind:'metric'|'action'
  id:RowFieldId|SessionTopbarActionId
  label:string
  description:string
}

export const SESSION_TOPBAR_ACTIONS: Array<{id:SessionTopbarActionId;label:string;description:string}> = [
  {id:'approvals',label:'Approvals',description:'Current approval mode and its chooser.'},
  ...DRAWER_TABS.map(tab=>({
    id:`drawer:${tab.id}` as SessionTopbarActionId,
    label:tab.label,
    description:tab.title,
  })),
]

export const SESSION_TOPBAR_CATALOG:SessionTopbarCatalogItem[] = [
  ...ROW_FIELDS.map(field=>({
    key:`metric:${field.id}`,kind:'metric' as const,id:field.id,label:field.label,
    description:field.notable,
  })),
  ...SESSION_TOPBAR_ACTIONS.map(action=>({
    key:`action:${action.id}`,kind:'action' as const,id:action.id,label:action.label,
    description:action.description,
  })),
]

export const sessionTopbarItemKey=(item:SessionTopbarItem):string=>`${item.kind}:${item.id}`

export function defaultSessionTopbarConfig():SessionTopbarConfig {
  return {
    version:SESSION_TOPBAR_VERSION,
    density:'standard',
    rows:[{
      left:[
        {kind:'metric',id:'title',mode:'always'},
        {kind:'metric',id:'cwd',mode:'notable'},
      ],
      right:[
        {kind:'action',id:'approvals'},
        {kind:'action',id:'drawer:queue'},
        {kind:'action',id:'drawer:transcript'},
      ],
      separator:'dot',
    }],
  }
}

const METRIC_IDS=new Set(ROW_FIELDS.map(field=>field.id))
const ACTION_IDS=new Set(SESSION_TOPBAR_ACTIONS.map(action=>action.id))

/** The renderings each styled field accepts; a field absent here has none. */
const METRIC_STYLES:Partial<Record<RowFieldId,readonly SessionTopbarMetricStyle[]>>={
  context:CONTEXT_ROW_RENDERS,
  cwd:CWD_STYLES,
}

const readStyle=(id:RowFieldId,raw:unknown):SessionTopbarMetricStyle|undefined=>{
  const allowed=METRIC_STYLES[id]
  return allowed&&allowed.includes(raw as SessionTopbarMetricStyle)?raw as SessionTopbarMetricStyle:undefined
}

function readItem(raw:unknown):SessionTopbarItem|null {
  if(!raw||typeof raw!=='object')return null
  const item=raw as {kind?:unknown;id?:unknown;mode?:unknown;style?:unknown}
  if(item.kind==='metric'&&typeof item.id==='string'&&METRIC_IDS.has(item.id as RowFieldId)){
    const id=item.id as RowFieldId
    const metric:SessionTopbarMetricItem={kind:'metric',id,mode:item.mode==='always'?'always':'notable'}
    const style=readStyle(id,item.style)
    if(style)metric.style=style
    return metric
  }
  if(item.kind==='action'&&typeof item.id==='string'&&ACTION_IDS.has(item.id as SessionTopbarActionId)){
    return {kind:'action',id:item.id as SessionTopbarActionId}
  }
  return null
}

function readItems(raw:unknown,seen:Set<string>):SessionTopbarItem[] {
  if(!Array.isArray(raw))return[]
  const out:SessionTopbarItem[]=[]
  for(const value of raw){
    const item=readItem(value)
    if(!item)continue
    const key=sessionTopbarItemKey(item)
    if(seen.has(key))continue
    seen.add(key);out.push(item)
  }
  return out
}

export function normalizeSessionTopbarConfig(raw:unknown):SessionTopbarConfig {
  const base=defaultSessionTopbarConfig()
  if(!raw||typeof raw!=='object')return base
  const source=raw as {version?:unknown;density?:unknown;rows?:unknown}
  const titleRemovable=typeof source.version==='number'&&source.version>=SESSION_TOPBAR_TITLE_REMOVABLE_VERSION
  const seen=new Set<string>()
  const rows:Array<SessionTopbarRow>=[]
  if(Array.isArray(source.rows))for(const value of source.rows.slice(0,SESSION_TOPBAR_MAX_ROWS)){
    if(!value||typeof value!=='object')continue
    const row=value as {left?:unknown;right?:unknown;separator?:unknown}
    rows.push({
      left:readItems(row.left,seen),
      right:readItems(row.right,seen),
      separator:typeof row.separator==='string'&&row.separator in SEPARATORS
        ?row.separator as SeparatorId:'dot',
    })
  }
  if(!rows.length)rows.push({left:[],right:[],separator:'dot'})
  if(!titleRemovable&&!seen.has('metric:title'))rows[0].left.unshift({kind:'metric',id:'title',mode:'always'})
  return {
    version:SESSION_TOPBAR_VERSION,
    density:['compact','standard','comfortable'].includes(String(source.density))
      ?source.density as SessionTopbarDensity:base.density,
    rows,
  }
}

const stripItem=(rows:SessionTopbarRow[],key:string):SessionTopbarRow[]=>rows.map(row=>({
  ...row,
  left:row.left.filter(item=>sessionTopbarItemKey(item)!==key),
  right:row.right.filter(item=>sessionTopbarItemKey(item)!==key),
}))

export function placeSessionTopbarItem(
  config:SessionTopbarConfig,item:SessionTopbarItem,rowIndex:number,align:RowAlign,index?:number,
):SessionTopbarConfig {
  const key=sessionTopbarItemKey(item)
  const rows=stripItem(config.rows,key)
  const target=rows[Math.max(0,Math.min(rowIndex,rows.length-1))]
  const items=[...target[align]]
  items.splice(index===undefined?items.length:Math.max(0,Math.min(index,items.length)),0,item)
  target[align]=items
  return normalizeSessionTopbarConfig({...config,rows})
}

/** Remove a placed item. Every item is removable, the title included: the overflow menu
 *  is fixed outside the catalog, so a bar with nothing placed still has its recovery path. */
export function removeSessionTopbarItem(config:SessionTopbarConfig,item:SessionTopbarItem):SessionTopbarConfig {
  return normalizeSessionTopbarConfig({...config,rows:stripItem(config.rows,sessionTopbarItemKey(item))})
}

/** Whether the layout places the title anywhere. */
export const sessionTopbarHasTitle=(config:SessionTopbarConfig):boolean=>
  config.rows.some(row=>[...row.left,...row.right].some(item=>item.kind==='metric'&&item.id==='title'))

export function setSessionTopbarMetricMode(
  config:SessionTopbarConfig,id:RowFieldId,mode:RowFieldMode,
):SessionTopbarConfig {
  const apply=(items:SessionTopbarItem[])=>items.map(item=>
    item.kind==='metric'&&item.id===id?{...item,mode}:item)
  return {...config,rows:config.rows.map(row=>({...row,left:apply(row.left),right:apply(row.right)}))}
}

/** Whether the editor offers a rendering choice for this metric. */
export const sessionTopbarMetricHasStyle=(id:RowFieldId):boolean=>id in METRIC_STYLES

export function setSessionTopbarMetricStyle(
  config:SessionTopbarConfig,id:RowFieldId,style:SessionTopbarMetricStyle,
):SessionTopbarConfig {
  if(readStyle(id,style)===undefined)return config
  const apply=(items:SessionTopbarItem[])=>items.map(item=>
    item.kind==='metric'&&item.id===id?{...item,style}:item)
  return {...config,rows:config.rows.map(row=>({...row,left:apply(row.left),right:apply(row.right)}))}
}

/**
 * The context rendering a placed top-bar metric draws with: its own `style`,
 * else the sidebar's when that is an in-row rendering, else a percentage.
 */
export function sessionTopbarContextRender(item:SessionTopbarMetricItem,rowConfig:SessionRowConfig):ContextRowRender {
  const own=readStyle('context',item.style)
  if(own)return own as ContextRowRender
  return CONTEXT_ROW_RENDERS.includes(rowConfig.context as ContextRowRender)
    ?rowConfig.context as ContextRowRender
    :'percent'
}

/** The rendering a styled top-bar metric draws with, for the editor's control. */
export function sessionTopbarMetricStyle(item:SessionTopbarMetricItem,rowConfig:SessionRowConfig):SessionTopbarMetricStyle|undefined {
  if(item.id==='context')return sessionTopbarContextRender(item,rowConfig)
  if(item.id==='cwd')return readStyle('cwd',item.style)??rowConfig.cwdStyle
  return undefined
}

/**
 * The row configuration a top-bar metric is rendered under.
 *
 * Identical to the sidebar's for every field but the styled ones: `context`,
 * whose sidebar setting names a surface (the indicator) the top bar does not
 * have, and `cwd`, which follows the sidebar unless the item chose otherwise.
 */
export function sessionTopbarRowConfig(item:SessionTopbarMetricItem,rowConfig:SessionRowConfig):SessionRowConfig {
  if(item.id==='context'){
    const context=sessionTopbarContextRender(item,rowConfig)
    return context===rowConfig.context?rowConfig:{...rowConfig,context}
  }
  if(item.id==='cwd'){
    const cwdStyle=readStyle('cwd',item.style) as CwdStyle|undefined
    return cwdStyle===undefined||cwdStyle===rowConfig.cwdStyle?rowConfig:{...rowConfig,cwdStyle}
  }
  return rowConfig
}

export function addSessionTopbarRow(config:SessionTopbarConfig):SessionTopbarConfig {
  if(config.rows.length>=SESSION_TOPBAR_MAX_ROWS)return config
  return {...config,rows:[...config.rows,{left:[],right:[],separator:'dot'}]}
}

export function removeSessionTopbarRow(config:SessionTopbarConfig,index:number):SessionTopbarConfig {
  if(config.rows.length<=1||index<0||index>=config.rows.length)return config
  const rows=config.rows.map(row=>({...row,left:[...row.left],right:[...row.right]}))
  const [removed]=rows.splice(index,1)
  const target=rows[Math.max(0,index-1)]
  target.left.push(...removed.left)
  target.right.push(...removed.right)
  return normalizeSessionTopbarConfig({...config,rows})
}

export function unplacedSessionTopbarItems(config:SessionTopbarConfig):SessionTopbarCatalogItem[] {
  const placed=new Set(config.rows.flatMap(row=>[...row.left,...row.right]).map(sessionTopbarItemKey))
  return SESSION_TOPBAR_CATALOG.filter(item=>!placed.has(item.key))
}
