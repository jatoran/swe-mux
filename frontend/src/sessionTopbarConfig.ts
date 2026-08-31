/** Browser-owned layout model for configurable session pane top bars. */

import { DRAWER_TABS, type DrawerTabId } from './drawerTabs.ts'
import {
  ROW_FIELDS, SEPARATORS, type RowAlign, type RowFieldId, type RowFieldMode, type SeparatorId,
} from './sessionRowConfig.ts'

export const SESSION_TOPBAR_VERSION = 1
export const SESSION_TOPBAR_MAX_ROWS = 3

export type SessionTopbarDensity = 'compact' | 'standard' | 'comfortable'
export type SessionTopbarActionId = 'approvals' | `drawer:${DrawerTabId}`
export type SessionTopbarMetricItem = { kind:'metric';id:RowFieldId;mode:RowFieldMode }
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

function readItem(raw:unknown):SessionTopbarItem|null {
  if(!raw||typeof raw!=='object')return null
  const item=raw as {kind?:unknown;id?:unknown;mode?:unknown}
  if(item.kind==='metric'&&typeof item.id==='string'&&METRIC_IDS.has(item.id as RowFieldId)){
    return {kind:'metric',id:item.id as RowFieldId,mode:item.mode==='always'?'always':'notable'}
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
  const source=raw as {density?:unknown;rows?:unknown}
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
  if(!seen.has('metric:title'))rows[0].left.unshift({kind:'metric',id:'title',mode:'always'})
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

export function removeSessionTopbarItem(config:SessionTopbarConfig,item:SessionTopbarItem):SessionTopbarConfig {
  if(item.kind==='metric'&&item.id==='title')return config
  return normalizeSessionTopbarConfig({...config,rows:stripItem(config.rows,sessionTopbarItemKey(item))})
}

export function setSessionTopbarMetricMode(
  config:SessionTopbarConfig,id:RowFieldId,mode:RowFieldMode,
):SessionTopbarConfig {
  const apply=(items:SessionTopbarItem[])=>items.map(item=>
    item.kind==='metric'&&item.id===id?{...item,mode}:item)
  return {...config,rows:config.rows.map(row=>({...row,left:apply(row.left),right:apply(row.right)}))}
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
