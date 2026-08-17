// Pick a CodeMirror language for a file, from its name.
//
// swe-mux views arbitrary repository files, so this maps the common extensions (and a few
// extension-less filenames like `Dockerfile`) onto CodeMirror 6 language support. Official
// grammars are used where they exist; the long tail rides the CodeMirror 5 legacy stream
// parsers wrapped by `StreamLanguage`. An unknown extension returns `null` — the editor then
// shows the file as plain text, still with line numbers, selection, search, and editing, just
// without token colours. That graceful fallback is the point: a viewer that never breaks on an
// unfamiliar file beats one that guesses wrong.

import { StreamLanguage, type StreamParser } from '@codemirror/language'
import type { Extension } from '@codemirror/state'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { json } from '@codemirror/lang-json'
import { css } from '@codemirror/lang-css'
import { html } from '@codemirror/lang-html'
import { markdown } from '@codemirror/lang-markdown'
import { rust } from '@codemirror/lang-rust'
import { cpp } from '@codemirror/lang-cpp'
import { java } from '@codemirror/lang-java'
import { php } from '@codemirror/lang-php'
import { sql } from '@codemirror/lang-sql'
import { xml } from '@codemirror/lang-xml'
import { yaml } from '@codemirror/lang-yaml'
import { go } from '@codemirror/lang-go'
import { shell } from '@codemirror/legacy-modes/mode/shell'
import { toml } from '@codemirror/legacy-modes/mode/toml'
import { ruby } from '@codemirror/legacy-modes/mode/ruby'
import { lua } from '@codemirror/legacy-modes/mode/lua'
import { dockerFile } from '@codemirror/legacy-modes/mode/dockerfile'
import { properties } from '@codemirror/legacy-modes/mode/properties'
import { diff } from '@codemirror/legacy-modes/mode/diff'
import { powerShell } from '@codemirror/legacy-modes/mode/powershell'
import { perl } from '@codemirror/legacy-modes/mode/perl'
import { r } from '@codemirror/legacy-modes/mode/r'
import { haskell } from '@codemirror/legacy-modes/mode/haskell'
import { swift } from '@codemirror/legacy-modes/mode/swift'
import { protobuf } from '@codemirror/legacy-modes/mode/protobuf'
import { nginx } from '@codemirror/legacy-modes/mode/nginx'
import { csharp, scala, kotlin, dart } from '@codemirror/legacy-modes/mode/clike'

/** A factory, so each editor gets its own extension instance rather than sharing one. */
type LanguageFactory = () => Extension

const stream = (parser: StreamParser<unknown>): LanguageFactory => () => StreamLanguage.define(parser)

// Extension → factory. Keep lowercase, no leading dot.
const BY_EXT: Readonly<Record<string, LanguageFactory>> = {
  // JavaScript / TypeScript family.
  js: () => javascript(),
  mjs: () => javascript(),
  cjs: () => javascript(),
  jsx: () => javascript({ jsx: true }),
  ts: () => javascript({ typescript: true }),
  mts: () => javascript({ typescript: true }),
  cts: () => javascript({ typescript: true }),
  tsx: () => javascript({ jsx: true, typescript: true }),
  // Python.
  py: () => python(),
  pyi: () => python(),
  pyw: () => python(),
  // Data / config.
  json: () => json(),
  jsonc: () => json(),
  json5: () => json(),
  webmanifest: () => json(),
  yaml: () => yaml(),
  yml: () => yaml(),
  toml: stream(toml),
  ini: stream(properties),
  cfg: stream(properties),
  conf: stream(properties),
  properties: stream(properties),
  env: stream(properties),
  // Web.
  css: () => css(),
  scss: () => css(),
  less: () => css(),
  html: () => html(),
  htm: () => html(),
  xhtml: () => html(),
  vue: () => html(),
  svelte: () => html(),
  xml: () => xml(),
  svg: () => xml(),
  xsl: () => xml(),
  xslt: () => xml(),
  xsd: () => xml(),
  plist: () => xml(),
  csproj: () => xml(),
  xaml: () => xml(),
  // Markdown (editable notes go through Continuity; this covers read-only/other markdown).
  md: () => markdown(),
  markdown: () => markdown(),
  mdx: () => markdown(),
  // Systems / compiled.
  rs: () => rust(),
  go: () => go(),
  c: () => cpp(),
  h: () => cpp(),
  cc: () => cpp(),
  cpp: () => cpp(),
  cxx: () => cpp(),
  hpp: () => cpp(),
  hh: () => cpp(),
  hxx: () => cpp(),
  ino: () => cpp(),
  java: () => java(),
  cs: stream(csharp),
  csx: stream(csharp),
  scala: stream(scala),
  sc: stream(scala),
  kt: stream(kotlin),
  kts: stream(kotlin),
  dart: stream(dart),
  swift: stream(swift),
  hs: stream(haskell),
  // Scripting.
  php: () => php(),
  rb: stream(ruby),
  gemspec: stream(ruby),
  rake: stream(ruby),
  lua: stream(lua),
  pl: stream(perl),
  pm: stream(perl),
  r: stream(r),
  sh: stream(shell),
  bash: stream(shell),
  zsh: stream(shell),
  ksh: stream(shell),
  ps1: stream(powerShell),
  psm1: stream(powerShell),
  psd1: stream(powerShell),
  // Database / IDL / misc.
  sql: () => sql(),
  mysql: () => sql(),
  pgsql: () => sql(),
  proto: stream(protobuf),
  diff: stream(diff),
  patch: stream(diff),
}

// Whole-filename matches, checked before the extension (extension-less or specially named files).
const BY_NAME: Readonly<Record<string, LanguageFactory>> = {
  dockerfile: stream(dockerFile),
  containerfile: stream(dockerFile),
  'nginx.conf': stream(nginx),
  gemfile: stream(ruby),
  rakefile: stream(ruby),
  '.gitconfig': stream(properties),
  '.npmrc': stream(properties),
  '.editorconfig': stream(properties),
  '.bashrc': stream(shell),
  '.zshrc': stream(shell),
  '.bash_profile': stream(shell),
  '.zprofile': stream(shell),
  '.profile': stream(shell),
  '.bash_aliases': stream(shell),
}

/** The CodeMirror language extension for a filename, or null for plain text. */
export function languageForFilename(name: string): Extension | null {
  const base = (name.split(/[\\/]/).pop() || name).toLowerCase()
  const named = BY_NAME[base]
  if (named) return named()
  const dot = base.lastIndexOf('.')
  const ext = dot > 0 ? base.slice(dot + 1) : dot === 0 ? base.slice(1) : ''
  const factory = BY_EXT[ext]
  return factory ? factory() : null
}
