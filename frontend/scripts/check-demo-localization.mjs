import { readFileSync, readdirSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { IntlMessageFormat } from 'intl-messageformat'
import { messageContract } from '../src/localization/catalogContract.ts'
const root = fileURLToPath(new URL('../', import.meta.url))
const en = JSON.parse(readFileSync(join(root, 'src/demo/catalogs/en.json'), 'utf8'))
const zh = JSON.parse(readFileSync(join(root, 'src/demo/catalogs/zh-Hans.json'), 'utf8'))
const errors = [], seen = new Set()
for (const id of new Set([...Object.keys(en), ...Object.keys(zh)])) {
  if (!en[id] || !zh[id]) { errors.push('Missing catalog value: ' + id); continue }
  try {
    if (messageContract(en[id]) !== messageContract(zh[id])) errors.push('Formatting contract differs: ' + id)
    new IntlMessageFormat(en[id], 'en', undefined, {ignoreTag: true})
    new IntlMessageFormat(zh[id], 'zh-Hans', undefined, {ignoreTag: true})
  } catch (error) { errors.push(id + ': ' + error) }
}
const allowed = new Set(['English', '简体中文', 'Vim', '← swemux.dev'])
function visitFile(path) {
  const source = readFileSync(path, 'utf8'), sf = ts.createSourceFile(path, source, 99, true)
  const file = relative(root, path).replaceAll('\\', '/')
  const raw = n => {
    if (!/[A-Za-z\u3400-\u9fff]/.test(n.text) || allowed.has(n.text.trim())) return
    errors.push(file + ':' + (sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1) + ' uncataloged demo UI: ' + n.text.trim())
  }
  function visible(n) {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) raw(n)
    else if (ts.isConditionalExpression(n)) { visible(n.whenTrue); visible(n.whenFalse) }
    else if (ts.isBinaryExpression(n) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(n.operatorToken.kind)) visible(n.right)
  }
  function visit(n) {
    if (ts.isCallExpression(n) && n.expression.getText(sf) === 'dt') {
      const id = n.arguments[0]?.text
      if (!id || !en[id]) errors.push(file + ': unknown demo message ' + id)
      else seen.add(id)
    }
    if (ts.isJsxText(n)) raw(n)
    if (ts.isJsxAttribute(n) && ['aria-label', 'title', 'placeholder', 'alt'].includes(n.name.getText(sf)) && n.initializer) {
      if (ts.isStringLiteral(n.initializer)) raw(n.initializer)
      else if (ts.isJsxExpression(n.initializer) && n.initializer.expression) visible(n.initializer.expression)
    }
    if (ts.isJsxExpression(n) && n.expression && (ts.isJsxElement(n.parent) || ts.isJsxFragment(n.parent))) visible(n.expression)
    ts.forEachChild(n, visit)
  }
  visit(sf)
}
for (const entry of readdirSync(join(root, 'src/demo'))) {
  if (/\.(ts|tsx)$/.test(entry) && entry !== 'keymapFixture.ts') visitFile(join(root, 'src/demo', entry))
}
if (errors.length) { console.error(errors.join('\n')); process.exitCode = 1 }
else console.log(Object.keys(en).length + ' demo messages: English/Chinese parity, ICU contracts, references and visible controls valid')
