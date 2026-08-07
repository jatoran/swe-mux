import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { filterModelOptions, type ModelOption } from './modelFilter'

type Props={
  id:string
  value:string
  options:ModelOption[]
  emptyLabel:string
  onChange:(value:string)=>void
}

export function ModelPicker({id,value,options,emptyLabel,onChange}:Props){
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
      {!query&&<button type="button" role="option" aria-selected={!value} class={!value?'active':''} onPointerDown={event=>{event.preventDefault();choose('')}}>{emptyLabel}</button>}
      {matches.map((model,index)=><button
        id={`${listId}-${index}`}
        type="button"
        role="option"
        aria-selected={model.id===value}
        class={index===active?'active':''}
        onMouseEnter={()=>setActive(index)}
        onPointerDown={event=>{event.preventDefault();choose(model.id)}}
      ><strong>{model.name}</strong><span>{model.id}</span></button>)}
      {!matches.length&&<span class="model-picker-empty">No matching models</span>}
    </div>}
  </div>
}
