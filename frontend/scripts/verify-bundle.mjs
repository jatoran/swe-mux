import { readFileSync, readdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Guards the built bundle against the dropped-declaration artifact that made every
// oh-my-pi pane render black: Rollup DCE removing a `let`/`var` enum declaration
// while keeping its IIFE assignment, leaving `(void 0||(x={}))` — a strict-mode
// ReferenceError the first time the code path runs. The crash class is silent at
// build time and kills xterm's write loop at run time, so the build itself must
// refuse to ship it. See scripts/patch-xterm-requestmode.mjs for the known
// instance; this scan is intentionally wider so a future dependency or toolchain
// upgrade reintroducing the shape anywhere fails loudly here instead of shipping
// a dead pane.
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
// Desktop packaging builds into a staging outDir and passes it here; the bare
// `npm run build` path publishes straight into src/swe_mux/static.
const assets = process.argv[2]
  ? resolve(process.argv[2])
  : resolve(frontend, '..', 'src', 'swe_mux', 'static', 'assets')
const artifact = /void 0\|\|\(\s*[A-Za-z_$][\w$]*\s*=\s*\{\}\)/

const scanned = []
const failures = []
for (const name of readdirSync(assets)) {
  if (!name.endsWith('.js')) continue
  scanned.push(name)
  const source = readFileSync(resolve(assets, name), 'utf8')
  const match = artifact.exec(source)
  if (match) {
    const at = match.index
    failures.push(`${name} at offset ${at}: ${source.slice(Math.max(0, at - 60), at + 40)}`)
  }
}

if (scanned.length === 0) {
  throw new Error(`verify-bundle scanned no .js assets under ${assets}; the output directory moved and this check is no longer guarding the build`)
}
if (failures.length > 0) {
  throw new Error(
    'Built bundle contains a dropped-declaration assignment (strict-mode ReferenceError at run time):\n'
    + failures.join('\n'),
  )
}
console.log(`verify-bundle: ${scanned.length} assets clean`)
