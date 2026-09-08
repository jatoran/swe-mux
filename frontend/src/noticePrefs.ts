// Advisory notices the operator has asked never to see again.
//
// A notice here is a sentence the UI draws about a fact it cannot change - "these Codex
// sessions keep spending the outgoing login until restarted" - and that an operator who
// already knows it does not need to read on every switch. "Never show this again" is a
// fact about the operator's understanding, not about a screen, so it lives in the
// server-persisted settings store under the canonical `desktop` profile (the same
// arrangement as the command rail and the session top bar): a notice hidden for good on
// the desktop must not come back on the phone, and a device that has never loaded the
// store shows every notice rather than guessing.
//
// The vocabulary is a closed set of ids on purpose. A free-form list would let a
// dismissed notice be misspelled into a second one that never matches; a union keeps
// every producer and the Settings control that undoes it naming the same thing.
import { useEffect, useState } from 'preact/hooks'
import { rawDomain, saveDomain } from './deviceSettings.ts'

const PROFILE='desktop' as const
const DOMAIN='notices' as const

/** Every notice that can be hidden for good, by id. Add here first; the id is what the
 *  stored document carries, so renaming one un-hides it on every install. */
export const NOTICE_IDS=['stranded-sessions'] as const
export type NoticeId=(typeof NOTICE_IDS)[number]

export type NoticePreferences={hidden:NoticeId[]}

const known=(value:unknown):value is NoticeId=>typeof value==='string'&&(NOTICE_IDS as readonly string[]).includes(value)

/** The stored document, or the shape of one. Unknown ids are dropped rather than kept:
 *  they are either a newer frontend's vocabulary, which this build cannot draw anyway,
 *  or a corrupted entry, and neither should be re-saved as if it were ours. Order is
 *  not meaningful, so the result is deduplicated and sorted for a stable digest. */
export function normalizeNoticePreferences(value:unknown):NoticePreferences{
  const raw=value&&typeof value==='object'?(value as Record<string,unknown>).hidden:undefined
  const hidden=Array.isArray(raw)?raw.filter(known):[]
  return {hidden:[...new Set(hidden)].sort()}
}

export const noticePreferences=():NoticePreferences=>normalizeNoticePreferences(rawDomain(PROFILE,DOMAIN))

export const isNoticeHidden=(id:NoticeId):boolean=>noticePreferences().hidden.includes(id)

/** Hide, or show again, one notice for good. Optimistic like every settings write: the
 *  cache updates and re-publishes before the daemon answers, and a failed write is
 *  retried by the next edit rather than reported here - the notice it governs is
 *  advisory, so a lost preference costs one more reading, not a fact. */
export function setNoticeHidden(id:NoticeId,hidden:boolean):Promise<void>{
  const current=noticePreferences().hidden.filter(entry=>entry!==id)
  const next=normalizeNoticePreferences({hidden:hidden?[...current,id]:current})
  return saveDomain(PROFILE,DOMAIN,next as unknown as Record<string,unknown>).catch(()=>{
    /* UI already updated optimistically; a later edit retries persistence. */
  })
}

/** The hidden set, kept current across every device's edits (the daemon republishes
 *  `settings_changed`, which `deviceSettings` turns into `mux:settings-changed`). */
export function useNoticePreferences():NoticePreferences{
  const [preferences,setPreferences]=useState(noticePreferences)
  useEffect(()=>{
    const sync=()=>setPreferences(noticePreferences())
    sync();window.addEventListener('mux:settings-changed',sync)
    return()=>window.removeEventListener('mux:settings-changed',sync)
  },[])
  return preferences
}
