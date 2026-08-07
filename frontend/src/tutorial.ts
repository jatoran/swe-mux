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
