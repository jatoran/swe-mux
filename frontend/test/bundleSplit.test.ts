import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { languageLoaderForFilename } from '../src/codeLanguage.ts'

/**
 * What stays out of the entry chunk, asserted where it can actually be checked.
 *
 * The initial bundle was 3.38MB raw / 1.06MB gzip, and three things in it were paid for by
 * every page load while being reachable only through a deliberate act: ~28 CodeMirror
 * grammars, CodeMirror's core, and Sigma + Graphology. Splitting them took the entry chunk
 * to 2.17MB / 654KB.
 *
 * A size number cannot be asserted here (this suite does not build), so what is asserted
 * is the *shape* that produces it: no static grammar import, and no static path from the
 * shell to either heavy surface. Each is a single import line away from silently
 * regressing, and the regression would be invisible until somebody measured again.
 */

const src = (...parts: string[]) => readFileSync(join(import.meta.dirname, '..', 'src', ...parts), 'utf8')

/** Source with `//` comment lines dropped, so prose about an import is not read as one. */
const code = (source: string) =>
  source.split('\n').filter(line => !line.trim().startsWith('//')).join('\n')

test('no grammar is imported statically', () => {
  const languages = code(src('codeLanguage.ts'))
  // The two allowed static imports are the wrapper and a type; every grammar is behind
  // `import()`, which is what makes each one its own chunk.
  assert.doesNotMatch(languages, /^import .*'@codemirror\/lang-/m)
  assert.doesNotMatch(languages, /^import .*'@codemirror\/legacy-modes/m)
  assert.match(languages, /import\('@codemirror\/lang-javascript'\)/)
  assert.match(languages, /import\('@codemirror\/legacy-modes\/mode\/shell'\)/)
})

test('every on-demand grammar is pre-bundled for the dev server', () => {
  // Dev answers a dependency discovered at *runtime* with a full page reload, and a
  // grammar behind `import()` is exactly that. In the renderer suite the reload lands
  // mid-spec and reads as an editor that stopped responding to typing - the same trap the
  // Change Map's layout worker already carries a note about in `vite.config.ts`. The two
  // lists have to move together, so a new grammar that forgets the second one fails here
  // rather than intermittently in Playwright.
  const config = readFileSync(join(import.meta.dirname, '..', 'vite.config.ts'), 'utf8')
  const included = new Set([...config.matchAll(/'(@codemirror\/[^']+)'/g)].map(match => match[1]))
  const imported = [...code(src('codeLanguage.ts')).matchAll(/import\('(@codemirror\/[^']+)'\)/g)]
    .map(match => match[1])
  assert.ok(imported.length > 20, 'the grammar list should not have quietly emptied')
  for (const specifier of imported) assert.ok(included.has(specifier), specifier)
})

test('the filename still picks the same language, without loading it', () => {
  // The lookup is synchronous and the grammar is not: naming a file must not fetch
  // anything, or the editor would pay for a chunk before it knows it needs one.
  assert.notEqual(languageLoaderForFilename('App.tsx'), null)
  assert.notEqual(languageLoaderForFilename('server.py'), null)
  assert.notEqual(languageLoaderForFilename('Dockerfile'), null)
  assert.notEqual(languageLoaderForFilename('deep/nested/.bashrc'), null)
  assert.notEqual(languageLoaderForFilename('C:\\repo\\Cargo.toml'), null)
  // Case-insensitive on both halves of the lookup, as before.
  assert.equal(languageLoaderForFilename('MAKEFILE.TS'), languageLoaderForFilename('makefile.ts'))
  // An unknown extension is plain text, not a broken editor.
  assert.equal(languageLoaderForFilename('notes.qqq'), null)
  assert.equal(languageLoaderForFilename('LICENSE'), null)
})

test('extensions that share a grammar share a loader', () => {
  // One `import()` target, one chunk, one fetch — `.yml` must not pull a second copy of
  // the YAML grammar just because it spells its extension differently.
  assert.equal(languageLoaderForFilename('a.yaml'), languageLoaderForFilename('b.yml'))
  assert.equal(languageLoaderForFilename('a.md'), languageLoaderForFilename('b.markdown'))
  assert.equal(languageLoaderForFilename('a.sh'), languageLoaderForFilename('.zshrc'))
  // ...and ones that do not share a grammar must not collide.
  assert.notEqual(languageLoaderForFilename('a.py'), languageLoaderForFilename('b.rs'))
})

test('CodeMirror is reached only through the lazy wrapper', () => {
  // `ProjectResource` is statically imported by the shell, so importing the editor from
  // it would put every CodeMirror package back in the entry chunk.
  const resource = code(src('ProjectResource.tsx'))
  assert.match(resource, /^import \{ LazyCodeEditor \} from '\.\/LazyCodeEditor'$/m)
  assert.doesNotMatch(resource, /^import .*from '\.\/CodeEditor'/m)
  assert.ok(resource.includes('<LazyCodeEditor'))
  assert.ok(!resource.includes('<CodeEditor'))
  assert.match(code(src('LazyCodeEditor.tsx')), /import\('\.\/CodeEditor'\)/)
})

test('Sigma and Graphology are reached only through the lazy wrapper', () => {
  for (const host of ['App.tsx', 'UtilityDrawer.tsx']) {
    const source = code(src(host))
    assert.match(source, /^import \{ LazyChangeMap \} from '\.\/LazyChangeMap'$/m, host)
    assert.doesNotMatch(source, /^import .*from '\.\/ChangeMapPane'/m, host)
    assert.ok(!source.includes('<ChangeMapPane'), host)
  }
  assert.match(code(src('LazyChangeMap.tsx')), /import\('\.\/ChangeMapPane'\)/)
  // Nothing else may reach the graph libraries directly.
  const pane = code(src('ChangeMapPane.tsx'))
  assert.match(pane, /^import Sigma from 'sigma'$/m)
  assert.match(pane, /^import Graph from 'graphology'$/m)
})

test('a lazy surface reserves its box rather than collapsing to nothing', () => {
  // Flash-of-nothing is the failure mode of a lazy route on a fast connection: the
  // placeholder has to occupy the same grid cell the real thing will, or the layout
  // jumps once per open. Both stand-ins carry the loaded component's own class.
  assert.match(code(src('LazyCodeEditor.tsx')), /class="code-editor code-editor-state"/)
  assert.match(code(src('LazyChangeMap.tsx')), /class="change-map-pane change-map-state"/)
  // ...and the caption is delayed, so a chunk that lands in a frame shows no message.
  const style = src('style.css')
  assert.match(style, /\.code-editor-state>span \{ opacity:0;animation:code-editor-wait [^}]*\.35s forwards \}/)
  assert.match(style, /\.change-map-state>span \{ opacity:0;animation:code-editor-wait [^}]*\.35s forwards \}/)
})

test('the editor dismisses its own echo before serializing the document', () => {
  // One full `doc.toString()` and an O(n) compare per keystroke, for a value the editor
  // itself had just emitted. The reference check has to come first to be worth anything.
  const editor = code(src('CodeEditor.tsx'))
  const reconcile = editor.slice(editor.lastIndexOf('useEffect(() => {'))
  const echoCheck = reconcile.indexOf('lastEmitted.current === value')
  const serialize = reconcile.indexOf('current.state.doc.toString()')
  assert.ok(echoCheck > -1 && serialize > -1)
  assert.ok(echoCheck < serialize, 'the echo check must precede the serialization')
  assert.ok(editor.includes('lastEmitted.current = text'))
  // A lagging echo is not an external change. Without the count, the effect running a
  // keystroke behind replaces the document with an older copy of itself and re-emits it -
  // characters dropped at human typing speed, the page wedged at machine speed.
  assert.ok(reconcile.includes('pendingEchoes.current > 0'))
  assert.ok(reconcile.indexOf('pendingEchoes.current > 0') < serialize)
  assert.ok(editor.includes('pendingEchoes.current += 1'))
})
