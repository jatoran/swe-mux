import type { ComponentChildren, JSX } from 'preact'
import { RowTokenView } from './SessionRowBody.tsx'
import { SEPARATORS, type SessionRowConfig } from './sessionRowConfig.ts'
import { sessionFieldToken, type SessionRowFleetFacts } from './sessionRowFields.ts'
import { useRowClock } from './sessionRowPrefs.ts'
import type { SessionTopbarActionId, SessionTopbarConfig, SessionTopbarItem } from './sessionTopbarConfig.ts'
import type { Session } from './types.ts'

type Props={
  session:Session
  config:SessionTopbarConfig
  rowConfig:SessionRowConfig
  facts:SessionRowFleetFacts
  title?:ComponentChildren
  renderAction:(id:SessionTopbarActionId)=>ComponentChildren
  menu:ComponentChildren
  preview?:boolean
  onContextMenu?:JSX.MouseEventHandler<HTMLDivElement>
  onDblClick?:JSX.MouseEventHandler<HTMLDivElement>
}

export function SessionTopbar({session,config,rowConfig,facts,title,renderAction,menu,preview=false,onContextMenu,onDblClick}:Props){
  const now=useRowClock(!preview)
  const context={...facts,now}
  const renderItem=(item:SessionTopbarItem):ComponentChildren=>{
    if(item.kind==='action')return renderAction(item.id)
    if(item.id==='title'&&title!==undefined)return title
    const token=sessionFieldToken(item.id,item.mode,session,rowConfig,context)
    return token?<span class="session-topbar-metric"><RowTokenView token={token} session={session} config={rowConfig}/></span>:null
  }
  return <div class="pane-bar session-topbar" data-density={config.density} onContextMenu={onContextMenu} onDblClick={onDblClick}>
    {config.rows.map((row,rowIndex)=>{
      const separator=SEPARATORS[row.separator].text
      const section=(items:SessionTopbarItem[],align:'left'|'right')=>{
        const rendered=items.map(item=>({key:`${item.kind}:${item.id}`,node:renderItem(item)})).filter(item=>item.node!==null&&item.node!==false)
        return <div class={`session-topbar-section ${align}${align==='right'?' pane-tools':''}`}>{rendered.map((item,index)=><div key={item.key} class="session-topbar-item">{index>0&&separator?<span class="session-topbar-separator" aria-hidden="true">{separator}</span>:null}{item.node}</div>)}</div>
      }
      return <div key={rowIndex} class="session-topbar-row">{section(row.left,'left')}{section(row.right,'right')}{rowIndex===0&&<div class="session-topbar-menu">{menu}</div>}</div>
    })}
  </div>
}
