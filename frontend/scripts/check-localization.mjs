import ts from 'typescript'
import {readFileSync,readdirSync,writeFileSync} from 'node:fs'
import {join,relative} from 'node:path'
import {IntlMessageFormat} from 'intl-messageformat'
import {messageContract} from '../src/localization/catalogContract.ts'
const root=new URL('../',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')
const sourceRoot=join(root,'src'),catalog=JSON.parse(readFileSync(join(sourceRoot,'localization/catalogs/en.json'),'utf8'))
const displayProps=new Set(['label','title','description','tooltip','placeholder','alt','aria-label','aria-description','caption','heading','hint','blurb','subtitle','emptyText','emptyLabel','confirmLabel','cancelLabel','buttonLabel','disabledReason','ariaLabel','filterPlaceholder','consequence','where','listLabel'])
const errors=[],raw=[],refs={},messageParameters={}
const files=dir=>readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?(e.name==='localization'||e.name==='demo'?[]:files(join(dir,e.name))):/\.(ts|tsx)$/.test(e.name)?[join(dir,e.name)]:[])
const textNode=n=>ts.isStringLiteral(n)||ts.isNoSubstitutionTemplateLiteral(n)||ts.isTemplateExpression(n)
const words=n=>/[a-zA-Z\u3400-\u9fff]/.test(ts.isTemplateExpression(n)?n.head.text+n.templateSpans.map(s=>s.literal.text).join(''):n.text)
function parameters(ast,result=new Set()){
 for(const part of ast){if(part.type!==0&&part.type!==7)result.add(part.value);if(part.options)for(const option of Object.values(part.options))parameters(option.value,result);if(part.children)parameters(part.children,result)}
 return [...result].sort()
}
for(const [id,pattern] of Object.entries(catalog))try{messageParameters[id]=parameters(new IntlMessageFormat(pattern,'en',undefined,{ignoreTag:true}).getAst())}catch(error){errors.push(id+': '+error)}
const target=JSON.parse(readFileSync(join(sourceRoot,'localization/catalogs/zh-Hans.json'),'utf8'))
for(const id of Object.keys(catalog))if(typeof target[id]!=='string'||!target[id].trim())errors.push('Missing Chinese translation: '+id)
for(const [id,pattern] of Object.entries(target)){
 if(!(id in catalog)){errors.push('Unknown Chinese message: '+id);continue}
 try{
  const a=parameters(new IntlMessageFormat(catalog[id],'en',undefined,{ignoreTag:true}).getAst())
  const b=parameters(new IntlMessageFormat(pattern,'zh-Hans',undefined,{ignoreTag:true}).getAst())
  if(JSON.stringify(a)!==JSON.stringify(b)||messageContract(catalog[id])!==messageContract(pattern))errors.push('Placeholder or conditional-branch mismatch: '+id)
 }catch(error){errors.push(id+': '+error)}
}
const verbatim=JSON.parse(readFileSync(join(sourceRoot,'localization/verbatim.json'),'utf8'))
if(new Set(verbatim).size!==verbatim.length)errors.push('Duplicate verbatim message id')
for(const id of verbatim)if(!(id in catalog)||target[id]!==catalog[id])errors.push('Technical example must remain verbatim: '+id)
for(const path of files(sourceRoot)){
 const file=relative(root,path).replaceAll('\\','/')
 // This standalone voice-model benchmark is a development entry, absent from production builds.
 if(path.endsWith('.d.ts')||path.endsWith('smartTurnLab.tsx'))continue
 const source=readFileSync(path,'utf8'),sf=ts.createSourceFile(path,source,99,true)
 const note=(n,kind)=>{
  if(!words(n))return
  // A recovery URL token is a protocol identifier, despite the helper's name.
  if(file==='src/rendererRecovery.ts'&&n.text==='manual')return
  if(ts.isJsxText(n)&&!/[A-Za-z\u3400-\u9fff]/.test(n.text.replace(/&[a-z]+;/gi,'')))return
  // Language self-names must remain readable in either locale.
  if(file==='src/LanguagePicker.tsx'&&['English','简体中文'].includes(n.text))return
  // This is the HTML tag -> search-entry-kind registry, not a rendered label.
  if(file==='src/settingsSearch.ts'&&n.text==='field'&&n.parent.getText(sf)==="label: 'field'")return
  raw.push({file,line:sf.getLineAndCharacterOfPosition(n.getStart(sf)).line+1,kind,text:ts.isTemplateExpression(n)?n.getText(sf):n.text,context:n.parent.getText(sf).slice(0,180)})
 }
 function visible(n){
  if(textNode(n)){note(n,'display expression');if(ts.isTemplateExpression(n))for(const span of n.templateSpans)visible(span.expression)}
  else if(ts.isConditionalExpression(n)){visible(n.whenTrue);visible(n.whenFalse)}
  else if(ts.isParenthesizedExpression(n))visible(n.expression)
  else if(ts.isBinaryExpression(n)&&n.operatorToken.kind===ts.SyntaxKind.PlusToken){visible(n.left);visible(n.right)}
  else if(ts.isBinaryExpression(n)&&[ts.SyntaxKind.BarBarToken,ts.SyntaxKind.QuestionQuestionToken,ts.SyntaxKind.AmpersandAmpersandToken].includes(n.operatorToken.kind))visible(n.right)
 }
 function visit(n){
  if(ts.isJsxText(n)&&n.text.trim())note(n,'JSX text')
  if(ts.isJsxExpression(n)&&n.expression&&(ts.isJsxElement(n.parent)||ts.isJsxFragment(n.parent)))visible(n.expression)
  if(ts.isJsxAttribute(n)&&displayProps.has(n.name.getText(sf))&&n.initializer){
   if(ts.isStringLiteral(n.initializer))note(n.initializer,'attribute')
   else if(ts.isJsxExpression(n.initializer)&&n.initializer.expression)visible(n.initializer.expression)
  }
  if(ts.isPropertyAssignment(n)&&displayProps.has(n.name.getText(sf).replace(/^['"]|['"]$/g,'')))visible(n.initializer)
  if(ts.isPropertyAssignment(n)&&ts.isObjectLiteralExpression(n.parent)&&ts.isVariableDeclaration(n.parent.parent)&&/_(DESCRIPTIONS|LABELS|DETAILS|HINTS|WORD)$/.test(n.parent.parent.name.getText(sf)))visible(n.initializer)
  if(ts.isJsxAttribute(n)&&n.name.getText(sf)==='fallback'&&n.initializer&&ts.isStringLiteral(n.initializer))note(n.initializer,'attribute')
  if(ts.isCallExpression(n)&&ts.isIdentifier(n.expression)&&/^set(Error|Notice|Toast|Feedback|StatusMessage)$/.test(n.expression.text)&&n.arguments[0])visible(n.arguments[0])
  if(ts.isArrowFunction(n)&&!ts.isBlock(n.body)&&(!n.type||/\bstring\b/.test(n.type.getText(sf)))){
    const name=ts.isVariableDeclaration(n.parent)?n.parent.name.getText(sf):''
    if(/(Label|Title|Caption|Summary|Tooltip|Description|Detail|Details|Reason|Heading|Line|Text|Age|Hint|Message)$/.test(name)||/^(describe|human|format)/.test(name))visible(n.body)
  }
  if(ts.isReturnStatement(n)&&n.expression){
    let owner=n.parent
    while(owner&&!ts.isSourceFile(owner)&&!ts.isFunctionLike(owner))owner=owner.parent
    if(owner&&ts.isFunctionLike(owner)&&(!owner.type||/\bstring\b/.test(owner.type.getText(sf)))){
      const name=owner.name?.getText(sf)||(ts.isVariableDeclaration(owner.parent)?owner.parent.name.getText(sf):'')
      if(/(Label|Title|Caption|Summary|Tooltip|Description|Detail|Details|Reason|Heading|Line|Text|Age|Hint|Message)$/.test(name)||/^(describe|human|format)/.test(name))visible(n.expression)
    }
  }
  if(ts.isCallExpression(n)&&ts.isIdentifier(n.expression)&&['t','rich'].includes(n.expression.text)&&n.arguments[0]&&ts.isStringLiteral(n.arguments[0])){
   const id=n.arguments[0].text
   let owner=n.parent
   while(owner&&!ts.isFunctionLike(owner)&&!ts.isSourceFile(owner))owner=owner.parent
   if(owner&&ts.isSourceFile(owner))errors.push(file+': translated text evaluated at module import: '+id)
   ;(refs[id]??=[]).push({file,line:sf.getLineAndCharacterOfPosition(n.getStart(sf)).line+1,values:n.arguments[1]?.getText(sf).slice(0,300)})
   if(!(id in catalog))errors.push(file+': unknown message '+id)
   else if(!n.arguments[1]||ts.isObjectLiteralExpression(n.arguments[1])){
     const supplied=n.arguments[1]?.properties.filter(ts.isPropertyAssignment).map(p=>p.name.getText(sf).replace(/^['"]|['"]$/g,'')).sort()||[]
     if(JSON.stringify(supplied)!==JSON.stringify(messageParameters[id]))errors.push(file+': incorrect arguments for '+id+'; expected '+messageParameters[id]+'; got '+supplied)
   }
  }
  ts.forEachChild(n,visit)
 }visit(sf)
}
if(process.argv.includes('--report')){
 writeFileSync(join(sourceRoot,'localization/sources.json'),JSON.stringify(refs,null,2)+'\n')
 console.log(raw.length+' raw display candidates')
 for(const item of raw.slice(0,90))console.log(item.file+':'+item.line+' '+item.kind+' '+item.text.replaceAll('\n',' ').slice(0,130))
}
{
 for(const item of raw)errors.push(item.file+':'+item.line+': uncatalogued '+item.kind+' '+item.text.trim().slice(0,100))
}
if(errors.length){console.error(errors.join('\n'));process.exitCode=1}
else console.log(Object.keys(catalog).length+' source messages; '+Object.keys(target).length+' Chinese translations; all message references and placeholders valid')
