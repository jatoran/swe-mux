import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const app=readFileSync(join(import.meta.dirname,'..','src','App.tsx'),'utf8')

const section=(start:string,end:string)=>{
  const from=app.indexOf(start)
  const to=app.indexOf(end,from+start.length)
  assert.ok(from>=0,`missing ${start}`)
  assert.ok(to>from,`missing ${end} after ${start}`)
  return app.slice(from,to)
}

test('tab context-menu openers target without activating',()=>{
  const sessionMenu=section('const openSessionMenu =','const openTabMenu=')
  assert.match(sessionMenu,/if\(source==='pane'\)/)
  assert.doesNotMatch(sessionMenu,/source!==['"]sidebar['"]|source!=['"]sidebar['"]/, 'tab/mobile sources must not focus the session')

  const tabMenu=section('const openTabMenu=','const beginWorkspaceTabDrag=')
  assert.doesNotMatch(tabMenu,/setFocusedViewId|setActiveId/, 'resource tab menus must not change focus')

  const stack=section("if(node.type==='stack')", "if(node.kind==='note')")
  assert.doesNotMatch(stack,/onContextMenu=\{event=>\{[^}]*activate\(\)/, 'desktop right-click must not call normal tab activation')
  assert.doesNotMatch(stack,/onContextMenu=\{event=>\{[^}]*setActiveId/, 'desktop right-click must not select a terminal')

  const menu=section('{contextMenu && <div', '{projectMenu && <div')
  assert.doesNotMatch(menu,/runNamedCommand\('pane\.stackNew'\)/, 'new-tab placement must use the menu target, not focused pane state')
  assert.match(menu,/spawnTerminal\(target\.project_id,'stack',undefined,target\.id\)/)
  assert.match(menu,/setPromptTargetId\(target\.id\)/, 'secondary prompt UI must retain the menu target after the menu closes')
})

test('mobile long-press consumes its follow-up click and background close preserves selection',()=>{
  const mobile=section('const mobileTab=(leaf:PaneLeaf)', 'const mobileUnifiedWorkspace=')
  const opener=mobile.slice(mobile.indexOf('const openMobileTabMenu='),mobile.indexOf('return <div'))
  assert.doesNotMatch(opener,/activateMobileTab/, 'long-press opener must not activate the target tab')
  assert.match(mobile,/mobileTabHeldRef\.current=false;return}activateMobileTab\(leaf\)/, 'ordinary taps must remain the only mobile activation path')

  const closeFocus=section('const focusAfterMobileClose=', 'const closeMobileTab=')
  assert.match(closeFocus,/if\(mobileProjection\.selected\?\.id!==leaf\.id\)return/, 'closing a background tab must not move focus')
})

test('file resource tabs expose scoped filesystem actions',()=>{
  const resolver=section('type FileMenuSource=', 'const sidebarPreviewRow=')
  assert.match(resolver,/menu\.leaf\.kind==='note'\?menu\.leaf\.id:''/, 'non-resource tabs must not resolve as files')
  assert.match(resolver,/identity\?\.kind!=='file'&&identity\?\.kind!=='worktree-file'/)
  assert.match(resolver,/\/api\/projects\/\$\{menu\.projectId\}\/reveal/)
  assert.match(resolver,/\.\.\.\(target\.worktree\?\{worktree:target\.worktree\}:\{\}\)/, 'worktree reveal must retain its registered root')

  const menu=section('{tabMenu&&<div', 'Close tab</button>')
  assert.match(menu,/fileMenuTarget\(tabMenu\)/)
  assert.match(menu,/>Open in default explorer<\/button>/)
  assert.match(menu,/>Copy full path<\/button>/)
  assert.match(menu,/Copy path from \{fileMenuTarget\(tabMenu\)!\.worktree\?'worktree':'project'\} root/)
})
