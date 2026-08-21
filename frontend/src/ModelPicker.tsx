import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
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
    {open&&<div class="model-picker-options" id={listId} role="listbox" aria-label="Available models">
      {!query&&!required&&<button type="button" role="option" aria-selected={!value} class={!value?'active':''} onPointerDown={event=>{event.preventDefault();choose('')}}>{emptyLabel}</button>}
      {matches.map((model,index)=>{
        const meta=modelMetaLabel(model)
        const detail=modelMetaTitle(model)
        return <button
          id={`${listId}-${index}`}
          type="button"
          role="option"
          aria-selected={model.id===value}
          class={index===active?'active':''}
          // The row ellipsizes both the id and the price cell on a narrow panel,
          // and the price order (input then output) is not guessable from the
          // figures. The title carries both in full.
          title={[model.name,model.id,detail].filter(Boolean).join('\n')}
          onMouseEnter={()=>setActive(index)}
          onPointerDown={event=>{event.preventDefault();choose(model.id)}}
        ><strong>{model.name}</strong><span class="model-picker-meta"><span>{model.id}</span>{meta&&<span class="model-picker-price">{meta}</span>}</span></button>
      })}
      {!matches.length&&<span class="model-picker-empty">No matching models</span>}
    </div>}
  </div>
}
