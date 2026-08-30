import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { applyTheme, browserColorScheme, configureCustomTheme, terminalThemes, themeDocumentPresentation, themeOptions, themePreviewColors } from '../src/theme.ts'

test('theme catalog has one entry for every selectable palette', () => {
  const optionNames=themeOptions.map(option=>option.name)
  assert.equal(new Set(optionNames).size,optionNames.length)
  assert.deepEqual(
    optionNames.filter(name=>name!=='system').sort(),
    Object.keys(terminalThemes).sort(),
  )
})

test('every concrete theme exposes a fixed six-color preview', () => {
  for(const option of themeOptions){
    if(option.name==='system')continue
    const colors=themePreviewColors(option.name)
    assert.equal(colors.length,6,option.name)
    assert.ok(colors.every(color=>/^#[0-9a-f]{6}$/i.test(color)),option.name)
  }
})

test('the initial document refuses unnecessary color transformations before scripts run', () => {
  const document=readFileSync(new URL('../index.html',import.meta.url),'utf8')
  const colorScheme=document.indexOf('<meta name="color-scheme" content="only dark" />')
  const darkReaderLock=document.indexOf('<meta name="darkreader-lock" />')
  const applicationScript=document.indexOf('<script type="module"')
  assert.ok(colorScheme>=0)
  assert.ok(darkReaderLock>colorScheme)
  assert.ok(applicationScript<0||darkReaderLock<applicationScript)
})

test('browser presentation follows the theme canvas', () => {
  assert.equal(browserColorScheme('#000000'),'dark')
  assert.equal(browserColorScheme('#ffffff'),'light')
  assert.deepEqual(themeDocumentPresentation('tokyo-night'),{
    resolved:'tokyo-night',
    background:terminalThemes['tokyo-night'].background,
    scheme:'dark',
  })
  assert.deepEqual(themeDocumentPresentation('catppuccin-latte'),{
    resolved:'catppuccin-latte',
    background:terminalThemes['catppuccin-latte'].background,
    scheme:'light',
  })
})

test('every fixed palette forbids browser color-scheme overrides', () => {
  const stylesheet=readFileSync(new URL('../src/style.css',import.meta.url),'utf8')
  assert.match(stylesheet,/^:root \{ color-scheme: only dark;/)
  for(const option of themeOptions){
    if(option.name==='system'||option.name==='custom')continue
    const declaration=new RegExp(`:root\\[data-theme="${option.name}"\\] \\{ color-scheme:only (light|dark);--bg:(#[0-9a-f]{6});`,'i')
    const match=stylesheet.match(declaration)
    assert.ok(match,option.name)
    assert.equal(match[1],themeDocumentPresentation(option.name).scheme,option.name)
    assert.equal(match[2],terminalThemes[option.name].background,option.name)
  }
})

test('applying a theme synchronizes the root and browser metadata', () => {
  const savedDocument=Object.getOwnPropertyDescriptor(globalThis,'document')
  const savedWindow=Object.getOwnPropertyDescriptor(globalThis,'window')
  const savedCustomTheme={...terminalThemes.custom}
  const metadata=new Map([
    ['meta[name="color-scheme"]',{content:'only dark'}],
    ['meta[name="theme-color"]',{content:'#0b0d10'}],
  ])
  const root={
    dataset:{} as Record<string,string>,
    style:{
      colorScheme:'',
      removeProperty:()=>'',
      setProperty:()=>{},
    },
  }
  const fakeDocument={
    documentElement:root,
    querySelector:(selector:string)=>{
      const meta=metadata.get(selector)
      return meta?{setAttribute:(_name:string,value:string)=>{meta.content=value}}:null
    },
  } as unknown as Document
  const fakeWindow=new EventTarget() as Window&typeof globalThis
  Object.defineProperty(globalThis,'document',{configurable:true,value:fakeDocument})
  Object.defineProperty(globalThis,'window',{configurable:true,value:fakeWindow})

  try {
    applyTheme('catppuccin-latte')
    assert.deepEqual(root.dataset,{theme:'catppuccin-latte',themeSelection:'catppuccin-latte'})
    assert.equal(root.style.colorScheme,'only light')
    assert.equal(metadata.get('meta[name="color-scheme"]')?.content,'only light')
    assert.equal(metadata.get('meta[name="theme-color"]')?.content,terminalThemes['catppuccin-latte'].background)

    configureCustomTheme({background:'#fafafa',panel:'#ffffff',line:'#cccccc',foreground:'#111111',muted:'#666666',accent:'#336699',error:'#aa2222'})
    applyTheme('custom')
    assert.equal(root.style.colorScheme,'only light')
    assert.equal(metadata.get('meta[name="color-scheme"]')?.content,'only light')
    assert.equal(metadata.get('meta[name="theme-color"]')?.content,'#fafafa')
  } finally {
    terminalThemes.custom=savedCustomTheme
    if(savedDocument)Object.defineProperty(globalThis,'document',savedDocument)
    else Reflect.deleteProperty(globalThis,'document')
    if(savedWindow)Object.defineProperty(globalThis,'window',savedWindow)
    else Reflect.deleteProperty(globalThis,'window')
  }
})
