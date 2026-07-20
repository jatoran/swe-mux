export const TERMINAL_DELETE = '\x7f'

/**
 * Convert an Android IME's editable-buffer replacement into terminal input.
 * Mobile keyboards frequently replace the whole composing word on every input
 * event. A terminal only understands edits at the cursor, so rewind the old
 * suffix with DEL and append the new suffix.
 */
export function mobileImeDelta(previous:string,next:string):string {
  const before=[...previous]
  const after=[...next]
  let shared=0
  while(shared<before.length&&shared<after.length&&before[shared]===after[shared])shared+=1
  const deleted=TERMINAL_DELETE.repeat(before.length-shared)
  const inserted=after.slice(shared).join('').replace(/\r\n|\r|\n/g,'\r')
  return deleted+inserted
}

export function isMobileTerminalInput():boolean {
  return window.matchMedia('(max-width: 760px), (pointer: coarse)').matches
}
