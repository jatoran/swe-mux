export function insertEditorTab(text:string,start:number,end:number):{text:string;caret:number} {
  return {text:`${text.slice(0,start)}\t${text.slice(end)}`,caret:start+1}
}
