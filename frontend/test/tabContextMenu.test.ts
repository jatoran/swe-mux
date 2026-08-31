import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const app=readFileSync(join(import.meta.dirname,'..','src','App.tsx'),'utf8')
const endedPane=readFileSync(join(import.meta.dirname,'..','src','EndedPaneBanner.tsx'),'utf8')

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
  assert.doesNotMatch(menu,/runNamedCommand\('pane\.stackNew'\)|spawnTerminal\(/, 'no session menu spawns a terminal: new work is the Run button, and a menu opened on some other session makes the landing pane a guess')
})

test('session menus act on the session and expose only the row-appearance settings shortcut',()=>{
  const menu=section('{contextMenu && <div', '{projectMenu && <div')
  // Copy working directory is the one pane-only row left, and it stays gated: sidebar,
  // desktop-tab and mobile-tab menus are opened to rename or kill.
  assert.match(menu,/\{contextMenu\.source==='pane'&&<button[^]*?session\.copyCwd/, 'copy-cwd must stay behind the pane gate')
  assert.equal(menu.split('session.copyCwd').length-1,1,'copy-cwd must appear once, gated')

  // Prompt library and Resources remain absent: both are a command and a drawer tab away.
  // Row appearance is the one surface shortcut because this menu is itself a rendered
  // session row, so it is the only place where the consequence is already under the pointer.
  assert.doesNotMatch(menu,/setPromptLibraryOpen/, 'the session menu must not open the prompt library')
  assert.doesNotMatch(menu,/processes\.open/, 'the session menu must not open the Resources dialog')
  assert.deepEqual(
    [...menu.matchAll(/openSettingTarget\('([^']+)'\)/g)].map(match=>match[1]),
    ['appearance.sessionRows'],
  )
  assert.match(menu,/>Configure appearance<\/span>/)
  // Clicking the row already opens the session; a row saying so was a second way to click.
  assert.doesNotMatch(menu,/runNamedCommand\('session\.open'\)/)
  // Boot timing is a fact about how the session started, read from the startup
  // diagnostics — not from the menu you open to rename or kill it.
  assert.doesNotMatch(menu,/startupSummary|startupTimingTitle|startup-chip/)

  // Every top-level row carries a mark of its own; the terminal skin's one `> ` per row
  // said the same thing on all of them and so distinguished none. The voice group's own
  // rows are excluded deliberately: three of them are a radio set already marked with a
  // `✓`, and an icon column beside a check column draws two marks for one fact.
  const top=menu.slice(0,menu.indexOf('<MenuGroup'))+menu.slice(menu.indexOf('</MenuGroup>'))
  const rows=top.match(/<button[^>]*onClick=/g)||[]
  const marked=top.match(/<button[^>]*class="menu-row[^"]*"[^>]*onClick=/g)||[]
  assert.ok(rows.length>8,'the session menu should still have its rows')
  assert.equal(rows.length,marked.length,'every session-menu row must be a menu-row with an icon')

  // Read state is one row whose label states the action, not a pair the reader
  // has to disambiguate.
  assert.match(menu,/\{isUnread\(contextMenu\.session,ackedTurns\)\?'Mark as read':'Mark as unread'\}/)
  assert.equal(menu.split("runNamedCommand('session.toggleRead')").length-1,1)

  // Read aloud is four rows for a setting most sessions never touch, so it sits
  // behind one collapsed group carrying its current mode.
  assert.match(menu,/<MenuGroup id="session-voice" label=\{`Read aloud · \$\{voiceModeLabel\(effectiveVoiceMode\(contextMenu\.session\)\)\}`\}/)
  assert.doesNotMatch(menu,/context-subtitle">READ ALOUD/)

  const tabMenu=section('{tabMenu&&<div', 'Close tab</button>')
  assert.doesNotMatch(tabMenu,/spawnTerminal\(/, 'resource tab menus do not spawn terminals either')
})

test('ended-session actions name and target the retained conversation directly',()=>{
  assert.doesNotMatch(app,/Resume as new/)
  assert.doesNotMatch(endedPane,/Resume as new/)
  assert.match(endedPane,/<button onClick=\{onResume\}>Resume<\/button>/)
  assert.match(
    app,
    /onOpenTranscript=\{hasHarnessTranscript\(session\.backend\)\?\(\)=>showHistoryEntry\(session\.agent_run_id\|\|session\.id\):undefined\}/,
    'the recovered-pane transcript action must name its own History run',
  )
  assert.match(
    app,
    /'POST', `\/api\/sessions\/\$\{session\.id\}\/resume`, \{\}/,
    'pane Resume must use the in-place retained-session transaction',
  )
})

test('the Project menu acts on this Project, each row with its own mark',()=>{
  const menu=section('{projectMenu && <div', '{sidebarMenu&&<div')

  // Four surfaces left because each is a drawer tab or a dialog that already opens on the
  // selected Project: right-clicking a Project row to reach them was a second route to a
  // place one click away.
  for(const gone of ['openNotesBrowser(target)','processes.project','queue.fleetProject','openProjectFiles(']){
    assert.ok(!menu.includes(gone),`${gone} must not be a Project-menu row`)
  }
  // Clicking a Project header is the fold, and long-press drag is the reorder path.
  assert.doesNotMatch(menu,/toggleProjectCollapsed/)
  assert.doesNotMatch(menu,/project\.moveUp|project\.moveDown/)
  // Category headers labelled three rows apiece in a menu of nine; the icons say it now.
  assert.doesNotMatch(menu,/context-subtitle">BROWSE THIS PROJECT|context-subtitle">PROJECT</)

  // A Group is a list, so it is the same pop-out the Maintenance and Run menus use — not
  // a native `<select>`, whose options a phone renders in a system sheet with none of this
  // menu's styling or keyboard walk.
  assert.doesNotMatch(menu,/context-select/)
  assert.match(menu,/<MenuGroup id="project-group"/)
  assert.match(menu,/Create new group/)
  // Creating from here moves this Project into what it creates, or the row is a detour.
  assert.match(menu,/setGroupEdit\(\{name:'',adoptProjectId:target\.id\}\)/)

  assert.match(menu,/>Rename</, 'the row says Rename; the menu already names the Project')
  assert.doesNotMatch(menu,/>Rename project</)
  // A trailing ellipsis on most of a menu says "this opens something" about rows that all
  // open something, so it stopped distinguishing them.
  assert.doesNotMatch(menu,/…<\/span>/)

  const rows=menu.match(/<button[^>]*onClick=/g)||[]
  const marked=menu.match(/<button[^>]*class="menu-row[^"]*"[^>]*onClick=/g)||[]
  assert.ok(rows.length>5,'the Project menu should still have its rows')
  assert.equal(rows.length,marked.length,'every Project-menu row must be a menu-row with an icon')
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
