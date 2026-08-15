import assert from 'node:assert/strict'
import test from 'node:test'
import {
  formatCommandLine,
  launchPreview,
  parseCommandLine,
  quoteArgument,
} from '../src/commandLine.ts'

test('the ordinary case is one line, which is the whole point', () => {
  assert.deepEqual(parseCommandLine('--model claude-opus-4-8'), [
    '--model',
    'claude-opus-4-8',
  ])
  assert.deepEqual(parseCommandLine('--permission-mode plan'), ['--permission-mode', 'plan'])
})

test('double quotes group an argument that contains spaces', () => {
  assert.deepEqual(
    parseCommandLine('--append-system-prompt "be terse and check your work"'),
    ['--append-system-prompt', 'be terse and check your work'],
  )
})

test('a backslash is literal, so Windows paths survive', () => {
  // The reason this is not shlex. A POSIX tokenizer eats every separator here and
  // the damage lands in an argument nobody inspects again.
  assert.deepEqual(parseCommandLine('--add-dir C:\\Users\\Jatora\\Projects'), [
    '--add-dir',
    'C:\\Users\\Jatora\\Projects',
  ])
  assert.deepEqual(parseCommandLine('--add-dir "C:\\Users\\My Docs"'), [
    '--add-dir',
    'C:\\Users\\My Docs',
  ])
})

test('a doubled quote inside a quoted run is a literal quote', () => {
  assert.deepEqual(parseCommandLine('-c "notify=[""mux""]"'), ['-c', 'notify=["mux"]'])
})

test('runs of whitespace collapse and leading or trailing space is ignored', () => {
  assert.deepEqual(parseCommandLine('   --model    opus   '), ['--model', 'opus'])
  assert.deepEqual(parseCommandLine(''), [])
  assert.deepEqual(parseCommandLine('   '), [])
})

test('an explicitly empty argument survives as an empty string', () => {
  assert.deepEqual(parseCommandLine('--tools ""'), ['--tools', ''])
})

test('a half-typed quote keeps what was typed rather than dropping it', () => {
  // This parses on every keystroke, so an unterminated quote is the normal state
  // mid-typing. Dropping the tail would delete the user's characters as they type.
  assert.deepEqual(parseCommandLine('--prompt "be ter'), ['--prompt', 'be ter'])
})

test('formatting quotes only what needs it', () => {
  assert.equal(formatCommandLine(['--model', 'claude-opus-4-8']), '--model claude-opus-4-8')
  assert.equal(quoteArgument('plain'), 'plain')
  assert.equal(quoteArgument('two words'), '"two words"')
  assert.equal(quoteArgument('has"quote'), '"has""quote"')
  assert.equal(quoteArgument(''), '""')
  // A path needs no quoting unless it contains a space, and never gains escapes.
  assert.equal(quoteArgument('C:\\Users\\Jatora'), 'C:\\Users\\Jatora')
})

test('every argv shape round trips through format and parse', () => {
  const cases = [
    ['--model', 'claude-opus-4-8'],
    ['--append-system-prompt', 'be terse and check your work'],
    ['--add-dir', 'C:\\Users\\My Docs'],
    ['-c', 'notify=["mux"]'],
    ['--tools', ''],
    ['--quote', 'say "hi"'],
  ]
  for (const argv of cases) {
    assert.deepEqual(parseCommandLine(formatCommandLine(argv)), argv, argv.join('|'))
  }
})

test('the launch preview shows the harness defaults ahead of the profile', () => {
  assert.equal(
    launchPreview('claude.exe', ['--global'], ['--model', 'claude-opus-4-8']),
    'claude.exe --global --model claude-opus-4-8',
  )
  // An agent profile usually names no executable of its own, and an empty entry
  // must not become a stray '""' in the preview.
  assert.equal(launchPreview('', [], ['--model', 'opus']), '--model opus')
})
