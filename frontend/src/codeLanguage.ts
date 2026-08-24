// Pick a CodeMirror language for a file, from its name.
//
// swe-mux views arbitrary repository files, so this maps the common extensions (and a few
// extension-less filenames like `Dockerfile`) onto CodeMirror 6 language support. Official
// grammars are used where they exist; the long tail rides the CodeMirror 5 legacy stream
// parsers wrapped by `StreamLanguage`. An unknown extension returns `null` — the editor then
// shows the file as plain text, still with line numbers, selection, search, and editing, just
// without token colours. That graceful fallback is the point: a viewer that never breaks on an
// unfamiliar file beats one that guesses wrong.
//
// Every grammar is loaded on demand. Statically importing all ~28 of them put the whole set
// into the entry chunk, so opening swe-mux paid for the Rust, PHP, Haskell, and Protobuf
// parsers whether or not a file was ever viewed — and a session that opens one `.ts` file
// still only needs one of them. Each `import()` below is its own Rollup chunk, fetched the
// first time a file of that kind is opened and cached by the browser after that. The editor
// mounts with the document already readable and swaps the grammar in through a compartment
// when it arrives (see `CodeEditor`), so nothing waits on the network to show text.

import { StreamLanguage, type StreamParser } from '@codemirror/language'
import type { Extension } from '@codemirror/state'

/** Resolves to the extension for one language. Called once per editor, so each gets its own. */
type LanguageLoader = () => Promise<Extension>

const stream = (load: () => Promise<StreamParser<unknown>>): LanguageLoader =>
  async () => StreamLanguage.define(await load())

const clike = (name: 'csharp' | 'scala' | 'kotlin' | 'dart'): LanguageLoader =>
  stream(async () => (await import('@codemirror/legacy-modes/mode/clike'))[name])

const javascript = (options?: { jsx?: boolean; typescript?: boolean }): LanguageLoader =>
  async () => (await import('@codemirror/lang-javascript')).javascript(options)

const python: LanguageLoader = async () => (await import('@codemirror/lang-python')).python()
const json: LanguageLoader = async () => (await import('@codemirror/lang-json')).json()
const css: LanguageLoader = async () => (await import('@codemirror/lang-css')).css()
const html: LanguageLoader = async () => (await import('@codemirror/lang-html')).html()
const markdown: LanguageLoader = async () => (await import('@codemirror/lang-markdown')).markdown()
const rust: LanguageLoader = async () => (await import('@codemirror/lang-rust')).rust()
const cpp: LanguageLoader = async () => (await import('@codemirror/lang-cpp')).cpp()
const java: LanguageLoader = async () => (await import('@codemirror/lang-java')).java()
const php: LanguageLoader = async () => (await import('@codemirror/lang-php')).php()
const sql: LanguageLoader = async () => (await import('@codemirror/lang-sql')).sql()
const xml: LanguageLoader = async () => (await import('@codemirror/lang-xml')).xml()
const yaml: LanguageLoader = async () => (await import('@codemirror/lang-yaml')).yaml()
const go: LanguageLoader = async () => (await import('@codemirror/lang-go')).go()

const shell = stream(async () => (await import('@codemirror/legacy-modes/mode/shell')).shell)
const toml = stream(async () => (await import('@codemirror/legacy-modes/mode/toml')).toml)
const ruby = stream(async () => (await import('@codemirror/legacy-modes/mode/ruby')).ruby)
const lua = stream(async () => (await import('@codemirror/legacy-modes/mode/lua')).lua)
const dockerFile = stream(async () => (await import('@codemirror/legacy-modes/mode/dockerfile')).dockerFile)
const properties = stream(async () => (await import('@codemirror/legacy-modes/mode/properties')).properties)
const diff = stream(async () => (await import('@codemirror/legacy-modes/mode/diff')).diff)
const powerShell = stream(async () => (await import('@codemirror/legacy-modes/mode/powershell')).powerShell)
const perl = stream(async () => (await import('@codemirror/legacy-modes/mode/perl')).perl)
const r = stream(async () => (await import('@codemirror/legacy-modes/mode/r')).r)
const haskell = stream(async () => (await import('@codemirror/legacy-modes/mode/haskell')).haskell)
const swift = stream(async () => (await import('@codemirror/legacy-modes/mode/swift')).swift)
const protobuf = stream(async () => (await import('@codemirror/legacy-modes/mode/protobuf')).protobuf)
const nginx = stream(async () => (await import('@codemirror/legacy-modes/mode/nginx')).nginx)

// Extension → loader. Keep lowercase, no leading dot.
const BY_EXT: Readonly<Record<string, LanguageLoader>> = {
  // JavaScript / TypeScript family.
  js: javascript(),
  mjs: javascript(),
  cjs: javascript(),
  jsx: javascript({ jsx: true }),
  ts: javascript({ typescript: true }),
  mts: javascript({ typescript: true }),
  cts: javascript({ typescript: true }),
  tsx: javascript({ jsx: true, typescript: true }),
  // Python.
  py: python,
  pyi: python,
  pyw: python,
  // Data / config.
  json,
  jsonc: json,
  json5: json,
  webmanifest: json,
  yaml,
  yml: yaml,
  toml,
  ini: properties,
  cfg: properties,
  conf: properties,
  properties,
  env: properties,
  // Web.
  css,
  scss: css,
  less: css,
  html,
  htm: html,
  xhtml: html,
  vue: html,
  svelte: html,
  xml,
  svg: xml,
  xsl: xml,
  xslt: xml,
  xsd: xml,
  plist: xml,
  csproj: xml,
  xaml: xml,
  // Markdown (editable notes go through Continuity; this covers read-only/other markdown).
  md: markdown,
  markdown,
  mdx: markdown,
  // Systems / compiled.
  rs: rust,
  go,
  c: cpp,
  h: cpp,
  cc: cpp,
  cpp,
  cxx: cpp,
  hpp: cpp,
  hh: cpp,
  hxx: cpp,
  ino: cpp,
  java,
  cs: clike('csharp'),
  csx: clike('csharp'),
  scala: clike('scala'),
  sc: clike('scala'),
  kt: clike('kotlin'),
  kts: clike('kotlin'),
  dart: clike('dart'),
  swift,
  hs: haskell,
  // Scripting.
  php,
  rb: ruby,
  gemspec: ruby,
  rake: ruby,
  lua,
  pl: perl,
  pm: perl,
  r,
  sh: shell,
  bash: shell,
  zsh: shell,
  ksh: shell,
  ps1: powerShell,
  psm1: powerShell,
  psd1: powerShell,
  // Database / IDL / misc.
  sql,
  mysql: sql,
  pgsql: sql,
  proto: protobuf,
  diff,
  patch: diff,
}

// Whole-filename matches, checked before the extension (extension-less or specially named files).
const BY_NAME: Readonly<Record<string, LanguageLoader>> = {
  dockerfile: dockerFile,
  containerfile: dockerFile,
  'nginx.conf': nginx,
  gemfile: ruby,
  rakefile: ruby,
  '.gitconfig': properties,
  '.npmrc': properties,
  '.editorconfig': properties,
  '.bashrc': shell,
  '.zshrc': shell,
  '.bash_profile': shell,
  '.zprofile': shell,
  '.profile': shell,
  '.bash_aliases': shell,
}

/** The loader for a filename, or null for plain text. Synchronous, so a caller can tell
 *  "no grammar" from "a grammar that has not arrived" without awaiting anything. */
export function languageLoaderForFilename(name: string): LanguageLoader | null {
  const base = (name.split(/[\\/]/).pop() || name).toLowerCase()
  const named = BY_NAME[base]
  if (named) return named
  const dot = base.lastIndexOf('.')
  const ext = dot > 0 ? base.slice(dot + 1) : dot === 0 ? base.slice(1) : ''
  return BY_EXT[ext] || null
}

/** The CodeMirror language extension for a filename, or null for plain text. */
export async function languageForFilename(name: string): Promise<Extension | null> {
  const loader = languageLoaderForFilename(name)
  return loader ? await loader() : null
}
