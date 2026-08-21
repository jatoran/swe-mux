import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { DROPDOWN_PRESS_SLOP_PX, dropdownScrollTop } from './dropdownOptions'
import { filterModelOptions, type ModelOption } from './modelFilter'
import { modelMetaLabel, modelMetaTitle } from './modelPricing'

/**
 * The one control every OpenRouter model setting uses.
 *
 * It is a filtering combobox rather than a `<select>` because the catalog is the
 * whole structured-output half of OpenRouter - hundreds of entries - which a
 * native select can only present as an unsearchable scroll, and on a phone as a
 * system wheel with no way to narrow it. It is not a free-text box either: every
 * one of these settings is validated as an *exact* model id, so a typo is a
 * silently dead feature rather than a rejected form.
 *
 * Each row states three things, in the order they are decided on: what the model
 * is called, which exact id that is, and what it costs. The id stays visible
 * despite reading much like the name, because the id is what the collapsed
 * control shows, what the config stores, and what the filter ranks on - a
 * search result whose match is invisible cannot be explained. Price shares that
 * second row rather than taking a third, in a right-aligned column so figures
 * line up down the list.
 */
type Props={
  id:string
  value:string
  options:ModelOption[]
  /**
   * Placeholder for the collapsed control, and - unless `required` - the label of
   * the row that clears the setting.
   */
  emptyLabel:string
  /**
   * Set for a setting the daemon rejects when blank: a model pinned for a
   * capability the routed defaults cannot guarantee, rather than an override of
   * them. Suppresses the clear-the-setting row, so the control cannot produce a
   * value that fails validation on Save.
   */
  required?:boolean
  onChange:(value:string)=>void
}

export function ModelPicker({id,value,options,emptyLabel,required,onChange}:Props){
  const root=useRef<HTMLDivElement>(null)
  const input=useRef<HTMLInputElement>(null)
  const list=useRef<HTMLDivElement>(null)
  const press=useRef<{x:number;y:number}|null>(null)
  const [open,setOpen]=useState(false)
  const [query,setQuery]=useState('')
  const [active,setActive]=useState(0)
  const matches=useMemo(()=>filterModelOptions(options,query),[options,query])
  const listId=`${id}-options`

  useEffect(()=>{
    if(!open)return
    const close=(event:PointerEvent)=>{
      if(!root.current?.contains(event.target as Node))setOpen(false)
    }
    document.addEventListener('pointerdown',close)
    return()=>document.removeEventListener('pointerdown',close)
  },[open])

  useEffect(()=>setActive(0),[query])

  // Opening lands on the model in force, roughly centred, instead of at the top of a
  // catalogue of hundreds. Without it the one thing the control is certain to be asked —
  // "which one is this now, and what is near it" — required scrolling to find out.
  useEffect(()=>{
    if(!open)return
    const container=list.current
    const row=container?.querySelector<HTMLElement>('[data-selected="true"]')
    if(!container||!row)return
    container.scrollTop=dropdownScrollTop({
      itemTop:row.offsetTop,
      itemHeight:row.offsetHeight,
      viewHeight:container.clientHeight,
      scrollHeight:container.scrollHeight,
      scrollTop:container.scrollTop,
    },'centre')
  },[open])

  const openPicker=()=>{
    setQuery('')
    setActive(0)
    setOpen(true)
  }
  const choose=(model:string)=>{
    onChange(model)
    setOpen(false)
    setQuery('')
    input.current?.focus()
  }
  const move=(step:number)=>{
    if(!open){openPicker();return}
    if(!matches.length)return
    setActive(current=>(current+step+matches.length)%matches.length)
  }

  return <div class="model-picker" ref={root}>
    <div class="model-picker-control">
      <input
        id={id}
        ref={input}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open&&matches[active]?`${listId}-${active}`:undefined}
        autocomplete="off"
        value={open?query:value}
        placeholder={open?'Type to filter models…':emptyLabel}
        onFocus={()=>{if(!open)openPicker()}}
        onInput={event=>{setQuery(event.currentTarget.value);setOpen(true)}}
        onKeyDown={event=>{
          if(event.key==='ArrowDown'){event.preventDefault();move(1)}
          else if(event.key==='ArrowUp'){event.preventDefault();move(-1)}
          else if(event.key==='Enter'&&open&&matches[active]){event.preventDefault();choose(matches[active].id)}
          else if(event.key==='Escape'&&open){event.preventDefault();setOpen(false);setQuery('')}
        }}
      />
      <button type="button" aria-label={open?'Close model list':'Open model list'} onPointerDown={event=>event.preventDefault()} onClick={()=>{if(open){setOpen(false);setQuery('')}else{openPicker();input.current?.focus()}}}>⌄</button>
    </div>
    {open&&<div ref={list} class="model-picker-options" id={listId} role="listbox" aria-label="Available models">
      {!query&&!required&&<button type="button" role="option" aria-selected={!value} class={!value?'active':''} data-selected={!value?'true':undefined} onClick={()=>choose('')}>{emptyLabel}</button>}
      {matches.map((model,index)=>{
        const meta=modelMetaLabel(model)
        const detail=modelMetaTitle(model)
        return <button
          id={`${listId}-${index}`}
          type="button"
          role="option"
          aria-selected={model.id===value}
          data-selected={model.id===value?'true':undefined}
          class={index===active?'active':''}
          // The row ellipsizes both the id and the price cell on a narrow panel,
          // and the price order (input then output) is not guessable from the
          // figures. The title carries both in full.
          title={[model.name,model.id,detail].filter(Boolean).join('\n')}
          onMouseEnter={()=>setActive(index)}
          onPointerDown={event=>{press.current={x:event.clientX,y:event.clientY}}}
          // `click`, never `pointerdown`. Committing on the press meant a finger that
          // landed on a row and dragged to scroll the catalogue chose that row instead
          // of scrolling — the reported defect. The browser already withholds a click
          // when a touch pans; the slop guard covers the slow drag that ends where it
          // started, and the mouse drag out of the list and back, which still clicks.
          onClick={event=>{
            const from=press.current
            press.current=null
            if(from&&Math.hypot(event.clientX-from.x,event.clientY-from.y)>DROPDOWN_PRESS_SLOP_PX)return
            choose(model.id)
          }}
        ><strong>{model.name}</strong><span class="model-picker-meta"><span>{model.id}</span>{meta&&<span class="model-picker-price">{meta}</span>}</span></button>
      })}
      {!matches.length&&<span class="model-picker-empty">No matching models</span>}
    </div>}
  </div>
}
