import { t } from './localization/index.ts'
export function insertEditorTab(text:string,start:number,end:number):{text:string;caret:number} {
  return {get text() { return t("editorText.cafa278f0b", { value1: text.slice(0,start), value2: text.slice(end) }) },caret:start+1}
}
