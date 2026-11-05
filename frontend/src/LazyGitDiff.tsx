import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import type { ComponentType } from 'preact'
import type { GitDiffViewProps } from './GitDiffView'

export function LazyGitDiff(props:GitDiffViewProps) {
  const [Renderer,setRenderer]=useState<ComponentType<GitDiffViewProps>|null>(null)
  const [error,setError]=useState('')
  useEffect(()=>{
    let live=true
    void import('./GitDiffView').then(module=>{if(live)setRenderer(()=>module.GitDiffView)}).catch(cause=>{if(live)setError(cause instanceof Error?cause.message:String(cause))})
    return()=>{live=false}
  },[])
  if(error)return <p class="git-diff-state error">{rich("LazyGitDiff.78ecbb8aee", { error: error })}</p>
  if(!Renderer)return <p class="git-diff-state">{t("LazyGitDiff.8d7030bb9b")}</p>
  return <Renderer {...props}/>
}
