/**
 * Legacy structural tests inspect JSX wiring as source text.
 * Expand catalog expressions into their English source representation, preserving
 * handlers, conditions and markup. Behavioral coverage lives in localization.spec.ts.
 */
import ts from 'typescript'
import { IntlMessageFormat } from 'intl-messageformat'
import english from '../../src/localization/catalogs/en.json' with { type: 'json' }

export function withCatalogText(source:string):string {
  source=source.replace(/\r\n/g,"\n")
  if(!/\b(t|rich)\(["']/.test(source))return source
  const sf=ts.createSourceFile('catalog-view.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
  const catalog=english as Record<string,string>
  const quote=(value:string)=>"'"+value.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/\n/g,'\\n')+"'"
  const literal=(value:string)=>String(new IntlMessageFormat(value,'en',undefined,{ignoreTag:true}).format())
  function message(node:ts.CallExpression,jsx:boolean):string|null {
    if(!ts.isIdentifier(node.expression)||!['t','rich'].includes(node.expression.text)||!node.arguments[0]||!ts.isStringLiteral(node.arguments[0]))return null
    const pattern=catalog[node.arguments[0].text]
    if(pattern===undefined)return null
    const params=new Map<string,ts.Expression>()
    if(node.arguments[1]&&ts.isObjectLiteralExpression(node.arguments[1]))for(const item of node.arguments[1].properties){
      if(ts.isPropertyAssignment(item))params.set(item.name.getText(sf).replace(/^['"]|['"]$/g,''),item.initializer)
    }
    const ast=new IntlMessageFormat(pattern,'en',undefined,{ignoreTag:true}).getAst()
    if(!params.size)return jsx?literal(pattern):quote(literal(pattern))
    const renderPattern=(parts:typeof ast):string=>parts.map(part=>{
      if(part.type===0)return jsx?part.value:part.value.replaceAll(String.fromCharCode(96),'\\'+String.fromCharCode(96))
      if(part.type===1||part.type===2||part.type===3||part.type===4){
        const value=params.get(part.value)
        if(!value)return '{'+part.value+'}'
        const text=render(value)
        return jsx?(ts.isJsxElement(value)||ts.isJsxSelfClosingElement(value)||ts.isJsxFragment(value)?text:'{'+text+'}'):'$'+'{'+text+'}'
      }
      if(part.type===5||part.type===6){
        const value=params.get(part.value)
        const yes=part.options.yes?.value,other=part.options.other?.value
        if(yes&&other&&value&&ts.isConditionalExpression(value)){
          const text=render(ts.isParenthesizedExpression(value.condition)?value.condition.expression:value.condition)+' ? '+quote(renderPattern(yes))+' : '+quote(renderPattern(other))
          return jsx?'{'+text+'}':'$'+'{'+text+'}'
        }
      }
      return ''
    }).join('')
    const text=renderPattern(ast),tick=String.fromCharCode(96)
    return jsx?text:tick+text+tick
  }
  function render(node:ts.Node):string {
    if(ts.isJsxExpression(node)&&node.expression&&ts.isCallExpression(node.expression)){
      const value=message(node.expression,!ts.isJsxAttribute(node.parent))
      if(value!==null)return ts.isJsxAttribute(node.parent)?(node.expression.arguments.length>1?'{'+value+'}':'"'+value.slice(1,-1).replace(/"/g,'&quot;')+'"'):value
    }
    if(ts.isGetAccessorDeclaration(node)&&node.body?.statements.length===1){
      const returned=node.body.statements[0]
      if(ts.isReturnStatement(returned)&&returned.expression&&ts.isCallExpression(returned.expression)){
        const value=message(returned.expression,false)
        if(value!==null)return node.name.getText(sf)+':'+value
      }
    }
    if(ts.isCallExpression(node)){
      const value=message(node,false)
      if(value!==null)return value
    }
    let at=node.getStart(sf),out=''
    ts.forEachChild(node,child=>{const start=child.getStart(sf);if(start<at||child.end>node.end)return;out+=source.slice(at,start)+render(child);at=child.end})
    return out+source.slice(at,node.end)
  }
  return source.slice(0,sf.getStart(sf))+render(sf)
}
