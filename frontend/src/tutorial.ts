export type TutorialStepId='welcome'|'projects'|'project-add'|'project-open'|'project-create'|'accounts'|'run'|'run-choice'|'workspace'|'new-tab'|'tabs'|'splits'|'resources'|'gates'|'features'|'feature-menu'|'configurator'|'ready'

/** Which collapsed chrome a step's anchor lives behind on the mobile layout.
 *
 *  A step that spotlights a control has to have that control on screen, and the phone
 *  layout keeps two panels shut by default. Getting this wrong is not cosmetic: a step
 *  carrying an `action` is satisfied by pressing its anchor, so an anchor that is not
 *  rendered leaves the step with nothing to press.
 *
 *  `resources` is the one that was wrong. Its anchor is the Notes control, which belongs
 *  to the **side panel** - the desktop launcher rail carries it while the panel is closed
 *  and the panel's own tab strip carries it once open - and it was opening the navigation
 *  sidebar, which has never carried it at all. */
export function mobileTutorialChrome(step:TutorialStepId):'sidebar'|'side-panel'|null {
  if(step==='projects'||step==='features'||step==='feature-menu'||step==='configurator')return 'sidebar'
  if(step==='resources')return 'side-panel'
  return null
}

/** The one first-run surface that may render, out of the two that exist.
 *
 *  They used to be decided independently and both were live on a fresh install: the tour
 *  opens synchronously from `localStorage`, the harness panel a config fetch later, and
 *  the panel's backdrop stacks over the tour's blur - so the product's first frame was a
 *  dialog over a doubly-dimmed app with an invisible tour card underneath it.
 *
 *  The harness panel **leads**: it decides what the launchers contain, and the tour walks
 *  the user into one of those launchers two steps in. It is also a bounded modal with
 *  three explicit exits, where a 14-step walk merely gets interrupted.
 *
 *  `configResolved` is why the tour does not simply render when the panel is absent:
 *  whether the panel is needed is a daemon fact that arrives after the first paint, so
 *  until it has settled the answer is "neither", not "the tour". Suppressing one fetch
 *  too late is exactly when the damage was done. */
export function firstRunSurface(state:{
  tutorialArmed:boolean
  configResolved:boolean
  harnessSetupNeeded:boolean
  settingsOpen:boolean
}):'harness'|'tutorial'|'none' {
  if(state.harnessSetupNeeded)return state.settingsOpen?'none':'harness'
  if(state.tutorialArmed&&state.configResolved)return 'tutorial'
  return 'none'
}

/**
 * Every piece of chrome the tour names by name, declared so it can be checked.
 *
 * The tour is prose about a moving app, and prose is exactly what nothing verifies. It
 * spent months telling every new user that the app menu had a `Utilities` group holding
 * the viewers; that group was unfolded into eight plain rows and the tour went on naming
 * it, because a string in a JSX body is invisible to every test in the suite. The audit
 * that opened Phase 16 found this by reading, which is not a mechanism.
 *
 * So the claims are data and `test/tourChrome.test.ts` closes the loop in both directions:
 * every name here must exist in the live chrome that owns it (`settingsTabs.ts` for a
 * Settings path, `App.tsx`'s rendered menu rows for a menu row), **and** every name here
 * must actually appear in the tour's copy, **and** every `Settings →` the copy contains
 * must be declared here. A renamed tab or a retired menu row fails the gate rather than
 * misdirecting a first-time user forever.
 *
 * Anchors are deliberately *not* listed: the same test derives them from the step list's
 * own `[data-tutorial="…"]` selectors and checks each against the components, so a
 * spotlight on a mark nobody renders fails without anything being declared twice.
 */
export const TUTORIAL_CHROME_CLAIMS: {
  /** Full `Settings → <tab label>` paths, exactly as the copy spells them. */
  settingsPaths: string[]
  /** Labels of rows in the app menu, exactly as `App.tsx` renders them. */
  menuRows: string[]
  /** Labels of collapsible groups in the app menu. */
  menuGroups: string[]
} = {
  settingsPaths: ['Settings → General', 'Settings → Accounts'],
  menuRows: [
    'Session history', 'Notes', 'Fleet queue', 'Prompt library', 'Clipboard history',
    'Resources', 'Usage & spend', 'Notifications',
    'Projects', 'Plugins', 'Configure Actions', 'Automation Dashboard', 'Settings', 'Help',
  ],
  menuGroups: ['Maintenance'],
}

export const TUTORIAL_STORAGE_KEY='mux.tutorial.v1'
export const TUTORIAL_VERSION='1'
export const TUTORIAL_ACTION_EVENT='mux:tutorial-action'

export type TutorialAction='project-created'|'account-saved'|'session-launched'|'tab-drag-started'|'tab-drag-cancelled'|'tab-dropped'
export type TutorialActionDetail={action:TutorialAction;backend?:string;zone?:'tabs'|'left'|'right'|'top'|'bottom'}
export type TutorialActionGate={action:TutorialAction;backend?:TutorialActionDetail['backend'];zone?:'tabs'|'split'}

export function emitTutorialAction(detail:TutorialActionDetail):void {
  window.dispatchEvent(new CustomEvent<TutorialActionDetail>(TUTORIAL_ACTION_EVENT,{detail}))
}

export function matchesTutorialAction(gate:TutorialActionGate,detail:TutorialActionDetail):boolean {
  if(gate.action!==detail.action)return false
  if(gate.backend&&gate.backend!==detail.backend)return false
  if(gate.zone==='tabs'&&detail.zone!=='tabs')return false
  if(gate.zone==='split'&&(!detail.zone||detail.zone==='tabs'))return false
  return true
}

type TutorialStorage=Pick<Storage,'getItem'|'setItem'|'removeItem'>

export function shouldStartTutorial(storage:TutorialStorage=localStorage):boolean {
  return storage.getItem(TUTORIAL_STORAGE_KEY)!==TUTORIAL_VERSION
}

export function completeTutorial(storage:TutorialStorage=localStorage):void {
  storage.setItem(TUTORIAL_STORAGE_KEY,TUTORIAL_VERSION)
}

export function resetTutorial(storage:TutorialStorage=localStorage):void {
  storage.removeItem(TUTORIAL_STORAGE_KEY)
}

export type TutorialCardPosition={left:number;top:number;side:'center'|'left'|'right'|'above'|'below'}

const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value))

export function placeTutorialCard(
  target:Pick<DOMRect,'left'|'right'|'top'|'bottom'|'width'|'height'>|null,
  viewport:{width:number;height:number},
  card:{width:number;height:number},
):TutorialCardPosition {
  const margin=16,gap=22
  const leftLimit=Math.max(margin,viewport.width-card.width-margin)
  const topLimit=Math.max(margin,viewport.height-card.height-margin)
  if(!target)return {left:clamp((viewport.width-card.width)/2,margin,leftLimit),top:clamp((viewport.height-card.height)/2,margin,topLimit),side:'center'}
  const centeredX=clamp(target.left+(target.width-card.width)/2,margin,leftLimit)
  const centeredY=clamp(target.top+(target.height-card.height)/2,margin,topLimit)
  if(viewport.width-target.right>=card.width+gap)return {left:target.right+gap,top:centeredY,side:'right'}
  if(target.left>=card.width+gap)return {left:target.left-card.width-gap,top:centeredY,side:'left'}
  if(viewport.height-target.bottom>=card.height+gap)return {left:centeredX,top:target.bottom+gap,side:'below'}
  if(target.top>=card.height+gap)return {left:centeredX,top:target.top-card.height-gap,side:'above'}
  return {left:clamp(viewport.width-card.width-margin,margin,leftLimit),top:margin,side:'center'}
}
