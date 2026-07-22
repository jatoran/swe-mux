import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const packageRoot = resolve(frontend, 'node_modules', '@xterm', 'addon-webgl')
const packageJson = JSON.parse(readFileSync(resolve(packageRoot, 'package.json'), 'utf8'))
const expectedVersion = '0.19.0'
const marker = 'swe-mux backport xterm.js#5991'
const checkOnly = process.argv.includes('--check')

if (packageJson.version !== expectedVersion) {
  throw new Error(`Refusing to patch @xterm/addon-webgl ${packageJson.version}; expected ${expectedVersion}. Re-evaluate the xterm.js#5991 backport.`)
}

function replaceOnce(source, before, after, file) {
  const occurrences = source.split(before).length - 1
  if (occurrences !== 1) throw new Error(`Expected one unpatched target in ${file}, found ${occurrences}`)
  return source.replace(before, after)
}

function patchFile(relativePath, transform) {
  const file = resolve(packageRoot, relativePath)
  const source = readFileSync(file, 'utf8')
  if (source.includes(marker)) return
  if (checkOnly) throw new Error(`${relativePath} is missing the xterm.js#5991 runtime backport; run npm install or npm run postinstall`)
  writeFileSync(file, transform(source, relativePath), 'utf8')
}

patchFile('src/WebglRenderer.ts', (source, file) => replaceOnce(
  source,
  `      line = terminal.buffer.lines.get(row)!;\n      this._model.lineLengths[y] = 0;`,
  `      line = terminal.buffer.lines.get(row)!;\n      this._model.lineLengths[y] = 0;\n      // ${marker}: buffer geometry may change between render scheduling and model update.\n      if (!line) {\n        for (x = 0; x < terminal.cols; x++) {\n          i = (y * terminal.cols + x) * RENDER_MODEL_INDICIES_PER_CELL;\n          this._glyphRenderer.value!.updateCell(x, y, NULL_CELL_CODE, 0, 0, 0, NULL_CELL_CHAR, 0, 0);\n          this._model.cells[i] = NULL_CELL_CODE;\n          this._model.cells[i + RENDER_MODEL_BG_OFFSET] = 0;\n          this._model.cells[i + RENDER_MODEL_FG_OFFSET] = 0;\n          this._model.cells[i + RENDER_MODEL_EXT_OFFSET] = 0;\n        }\n        modelUpdated = true;\n        continue;\n      }`,
  file,
))

patchFile('lib/addon-webgl.mjs', (source, file) => {
  source = replaceOnce(
    source,
    'for(a=t;a<=n;a++)for(l=a+s.buffer.ydisp,u=s.buffer.lines.get(l),this._model.lineLengths[a]=0,M=ue===l,h=0,c=this._characterJoinerService.getJoinedCharacters(l),y=0;y<s.cols;y++){',
    `for(a=t;a<=n;a++){/* ${marker} */if(l=a+s.buffer.ydisp,u=s.buffer.lines.get(l),this._model.lineLengths[a]=0,!u){for(y=0;y<s.cols;y++)E=(a*s.cols+y)*Ce,this._glyphRenderer.value.updateCell(y,a,0,0,0,0,pn,0,0),this._model.cells[E]=0,this._model.cells[E+ze]=0,this._model.cells[E+qe]=0,this._model.cells[E+Ct]=0;se=!0;continue}for(M=ue===l,h=0,c=this._characterJoinerService.getJoinedCharacters(l),y=0;y<s.cols;y++){`,
    file,
  )
  return replaceOnce(
    source,
    'y--}}se&&this._rectangleRenderer.value.updateBackgrounds(this._model)',
    'y--}}}se&&this._rectangleRenderer.value.updateBackgrounds(this._model)',
    file,
  )
})

patchFile('lib/addon-webgl.js', (source, file) => {
  source = replaceOnce(
    source,
    'for(n=e;n<=t;n++)for(r=n+i.buffer.ydisp,o=i.buffer.lines.get(r),this._model.lineLengths[n]=0,_=R===r,y=0,a=this._characterJoinerService.getJoinedCharacters(r),v=0;v<i.cols;v++){',
    `for(n=e;n<=t;n++){/* ${marker} */if(r=n+i.buffer.ydisp,o=i.buffer.lines.get(r),this._model.lineLengths[n]=0,!o){for(v=0;v<i.cols;v++)p=(n*i.cols+v)*d.RENDER_MODEL_INDICIES_PER_CELL,this._glyphRenderer.value.updateCell(v,n,h.NULL_CELL_CODE,0,0,0,h.NULL_CELL_CHAR,0,0),this._model.cells[p]=h.NULL_CELL_CODE,this._model.cells[p+d.RENDER_MODEL_BG_OFFSET]=0,this._model.cells[p+d.RENDER_MODEL_FG_OFFSET]=0,this._model.cells[p+d.RENDER_MODEL_EXT_OFFSET]=0;x=!0;continue}for(_=R===r,y=0,a=this._characterJoinerService.getJoinedCharacters(r),v=0;v<i.cols;v++){`,
    file,
  )
  return replaceOnce(
    source,
    'v--}}x&&this._rectangleRenderer.value.updateBackgrounds(this._model)',
    'v--}}}x&&this._rectangleRenderer.value.updateBackgrounds(this._model)',
    file,
  )
})

console.log(`@xterm/addon-webgl ${expectedVersion} xterm.js#5991 backport verified`)
