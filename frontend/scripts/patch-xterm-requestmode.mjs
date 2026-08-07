import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// @xterm/xterm 6.0.0's `requestMode` (the DECRQM / DECANM mode-report handler) opens
// with a function-local TypeScript enum, shipped in lib/xterm.mjs as
// `let r;((P)=>{...})(r||={})`. Rollup's dead-code elimination judges the `let r`
// declaration removable (the enum object is never read; the handler uses numeric
// literals), rewrites the read half of `r||={}` to `void 0`, and keeps the
// assignment — emitting `(void 0||(i={}))`, a strict-mode ReferenceError. Every
// CSI $ p query then throws inside xterm's write loop, which kills the loop
// permanently: the pane renders nothing from that byte on. Claude and Codex never
// send DECRQM; oh-my-pi probes five modes at startup, so every OMP pane died on
// attach. The enum is unused, so the fix deletes it before the bundler can mangle
// it. `scripts/verify-bundle.mjs` guards the built output against the same
// artifact reappearing anywhere else.
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const packageRoot = resolve(frontend, 'node_modules', '@xterm', 'xterm')
const packageJson = JSON.parse(readFileSync(resolve(packageRoot, 'package.json'), 'utf8'))
const expectedVersion = '6.0.0'
const marker = 'swe-mux fix: requestMode local enum vs Rollup DCE'
const checkOnly = process.argv.includes('--check')

if (packageJson.version !== expectedVersion) {
  throw new Error(`Refusing to patch @xterm/xterm ${packageJson.version}; expected ${expectedVersion}. Re-check whether requestMode still declares a function-local enum before carrying this patch forward.`)
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
  if (checkOnly) throw new Error(`${relativePath} is missing the requestMode enum fix; run npm install or npm run postinstall`)
  writeFileSync(file, transform(source, relativePath), 'utf8')
}

// Only the ESM build carries the hazard: Vite bundles lib/xterm.mjs, and only there
// does the enum survive as a `let`-declared IIFE. lib/xterm.js (the UMD build) was
// minified by a pipeline that already flattened the enum to numeric literals.
patchFile('lib/xterm.mjs', (source, file) => replaceOnce(
  source,
  'requestMode(e,i){let r;(P=>(P[P.NOT_RECOGNIZED=0]="NOT_RECOGNIZED",P[P.SET=1]="SET",P[P.RESET=2]="RESET",P[P.PERMANENTLY_SET=3]="PERMANENTLY_SET",P[P.PERMANENTLY_RESET=4]="PERMANENTLY_RESET"))(r||={});',
  `requestMode(e,i){/* ${marker} */`,
  file,
))

console.log(`@xterm/xterm ${expectedVersion} requestMode enum fix verified`)
