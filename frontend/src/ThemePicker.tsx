import { useEffect, useRef, useState } from 'preact/hooks'
import { themeOptions, themePreviewColors, type CustomTheme, type ThemeName } from './theme'

function ThemeSwatches({name,customTheme}:{name:ThemeName;customTheme:CustomTheme}) {
  return <span class="theme-swatches" aria-hidden="true">
    {themePreviewColors(name,customTheme).map((color,index)=><i key={`${color}-${index}`} style={{backgroundColor:color}} />)}
  </span>
}

export function ThemePicker({value,customTheme,open,onOpenChange,onChange,onPreview}:{
  value:ThemeName
  customTheme:CustomTheme
  open:boolean
  onOpenChange:(open:boolean)=>void
  onChange:(value:ThemeName)=>void
  /** Show a theme without choosing it; `null` hands the screen back to `value`. */
  onPreview?:(value:ThemeName|null)=>void
}) {
  const root=useRef<HTMLDivElement>(null)
  const trigger=useRef<HTMLButtonElement>(null)
  const list=useRef<HTMLDivElement>(null)
  const wasOpen=useRef(false)
  const selectedIndex=Math.max(0,themeOptions.findIndex(option=>option.name===value))
  const [activeIndex,setActiveIndex]=useState(selectedIndex)
  const selected=themeOptions[selectedIndex]

  useEffect(()=>{
    if(open){
      setActiveIndex(selectedIndex)
      const frame=requestAnimationFrame(()=>list.current?.focus())
      wasOpen.current=true
      return()=>cancelAnimationFrame(frame)
    }
    if(wasOpen.current)trigger.current?.focus()
    wasOpen.current=false
  },[open,selectedIndex])

  // Highlighting a theme shows it immediately, so the catalogue can be walked and
  // seen rather than chosen blind, reopened, and chosen again. Nothing is committed:
  // the draft moves only on `onChange`, and leaving the list any way at all — Enter,
  // click, Escape, a click outside — hands the screen back to the chosen value.
  // Escape is why this watches `open` rather than hooking the close handlers: the
  // dismiss stack closes this level by setting the flag, without calling back.
  // There is deliberately no unmount cleanup. Discarding unsaved settings already
  // re-applies the *saved* theme on its way out, and a revert-to-draft firing after
  // that would put the discarded choice back on screen.
  useEffect(()=>{
    if(!onPreview)return
    onPreview(open?themeOptions[activeIndex]?.name??null:null)
  },[open,activeIndex,onPreview])

  useEffect(()=>{
    if(!open)return
    const closeOutside=(event:PointerEvent)=>{
      if(!root.current?.contains(event.target as Node))onOpenChange(false)
    }
    window.addEventListener('pointerdown',closeOutside,true)
    return()=>window.removeEventListener('pointerdown',closeOutside,true)
  },[open,onOpenChange])

  const choose=(index:number)=>{
    const option=themeOptions[index]
    if(!option)return
    onChange(option.name)
    onOpenChange(false)
  }
  const move=(offset:number)=>setActiveIndex(current=>(current+offset+themeOptions.length)%themeOptions.length)
  const onListKeyDown=(event:KeyboardEvent)=>{
    if(event.key==='ArrowDown'){event.preventDefault();move(1)}
    else if(event.key==='ArrowUp'){event.preventDefault();move(-1)}
    else if(event.key==='Home'){event.preventDefault();setActiveIndex(0)}
    else if(event.key==='End'){event.preventDefault();setActiveIndex(themeOptions.length-1)}
    else if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(activeIndex)}
  }
  const onTriggerKeyDown=(event:KeyboardEvent)=>{
    if(event.key!=='ArrowDown'&&event.key!=='ArrowUp')return
    event.preventDefault()
    setActiveIndex(event.key==='ArrowDown'?selectedIndex:(selectedIndex-1+themeOptions.length)%themeOptions.length)
    onOpenChange(true)
  }

  return <div class="theme-picker" ref={root}>
    <button
      ref={trigger}
      type="button"
      class="theme-picker-trigger theme-picker-row"
      aria-label={`Theme, ${selected.label}`}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls="theme-picker-options"
      onClick={()=>onOpenChange(!open)}
      onKeyDown={onTriggerKeyDown}
    >
      <span>{selected.label}</span>
      <ThemeSwatches name={selected.name} customTheme={customTheme} />
      <span class="theme-picker-chevron" aria-hidden="true">{open?'▴':'▾'}</span>
    </button>
    {open&&<div
      ref={list}
      id="theme-picker-options"
      class="theme-picker-options"
      role="listbox"
      tabIndex={0}
      aria-label="Theme"
      aria-activedescendant={`theme-option-${themeOptions[activeIndex].name}`}
      onKeyDown={onListKeyDown}
    >
      {themeOptions.map((option,index)=><div
        id={`theme-option-${option.name}`}
        key={option.name}
        class={`theme-picker-option theme-picker-row${index===activeIndex?' active':''}`}
        role="option"
        aria-selected={option.name===value}
        onPointerMove={()=>setActiveIndex(index)}
        onClick={()=>choose(index)}
      >
        <span>{option.label}</span>
        <ThemeSwatches name={option.name} customTheme={customTheme} />
        <span class="theme-picker-check" aria-hidden="true">{option.name===value?'✓':''}</span>
      </div>)}
    </div>}
  </div>
}
