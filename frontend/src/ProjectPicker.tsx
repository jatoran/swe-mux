import { useEffect, useMemo, useRef, useState } from 'preact/hooks'

// A project's registry id is opaque, so — unlike ModelPicker, whose value is the
// human-readable model id — the closed control shows the selected project's name
// while onChange still carries the id. Typing filters on name and root path, the
// two fields a user actually recognises a checkout by (two Projects can share a
// name across different folders, so the root has to be searchable too).
export type ProjectPickerOption = { id:string; name:string; hint:string; visible:boolean }

type Props={
  id:string
  value:string
  options:ProjectPickerOption[]
  placeholder:string
  onChange:(id:string)=>void
}

function rank(option:ProjectPickerOption,needle:string):number{
  const name=option.name.toLocaleLowerCase()
  const hint=option.hint.toLocaleLowerCase()
  return name===needle?0:name.startsWith(needle)?1:name.includes(needle)?2:hint.includes(needle)?3:-1
}

export function ProjectPicker({id,value,options,placeholder,onChange}:Props){
  const root=useRef<HTMLDivElement>(null)
  const input=useRef<HTMLInputElement>(null)
  const [open,setOpen]=useState(false)
  const [query,setQuery]=useState('')
  const [active,setActive]=useState(0)
  const selected=options.find(option=>option.id===value)||null
  const matches=useMemo(()=>{
    const needle=query.trim().toLocaleLowerCase()
    if(!needle)return options
    return options
      .map((option,index)=>({option,index,score:rank(option,needle)}))
      .filter(item=>item.score>=0)
      .sort((left,right)=>left.score-right.score||left.index-right.index)
      .map(item=>item.option)
  },[options,query])
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

  const openPicker=()=>{setQuery('');setActive(0);setOpen(true)}
  const choose=(project:string)=>{
    onChange(project)
    setOpen(false)
    setQuery('')
    input.current?.blur()
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
        value={open?query:selected?.name||''}
        placeholder={open?'Type to filter projects…':placeholder}
        onFocus={()=>{if(!open)openPicker()}}
        onInput={event=>{setQuery(event.currentTarget.value);setOpen(true)}}
        onKeyDown={event=>{
          if(event.key==='ArrowDown'){event.preventDefault();move(1)}
          else if(event.key==='ArrowUp'){event.preventDefault();move(-1)}
          else if(event.key==='Enter'&&open&&matches[active]){event.preventDefault();choose(matches[active].id)}
          else if(event.key==='Escape'&&open){event.preventDefault();setOpen(false);setQuery('')}
        }}
      />
      <button type="button" aria-label={open?'Close project list':'Open project list'} onPointerDown={event=>event.preventDefault()} onClick={()=>{if(open){setOpen(false);setQuery('')}else{openPicker();input.current?.focus()}}}>⌄</button>
    </div>
    {open&&<div class="model-picker-options" id={listId} role="listbox" aria-label="Configured projects">
      {matches.map((option,index)=><button
        id={`${listId}-${index}`}
        type="button"
        role="option"
        aria-selected={option.id===value}
        class={index===active?'active':''}
        onMouseEnter={()=>setActive(index)}
        onPointerDown={event=>{event.preventDefault();choose(option.id)}}
      ><strong><span class={`project-visibility-dot ${option.visible?'visible':'hidden'}`} aria-hidden="true"/>{option.name}</strong><span>{option.hint}</span></button>)}
      {!matches.length&&<span class="model-picker-empty">No matching projects</span>}
    </div>}
  </div>
}
