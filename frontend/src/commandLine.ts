/**
 * Turning a typed command line into argv, and argv back into a command line.
 *
 * Launch profiles and per-harness defaults both store `string[]`, because that is
 * what a spawn is: an executable plus a list of arguments, handed to the OS with no
 * shell in between. The old editors exposed that list directly - one argv token per
 * line in one place, a JSON array in the other - and both were correct and
 * unguessable. Typing `--model claude-opus-4-8` the obvious way produced a single
 * argument, which the CLI then rejected as an unknown option.
 *
 * So the storage stays argv and only the entry changes: you type a command line and
 * this parses it. The rules are the ones a Windows user already has:
 *
 * - Whitespace separates arguments.
 * - Double quotes group, and are removed. `"be terse and careful"` is one argument.
 * - `""` inside a quoted run is a literal quote, matching `CommandLineToArgvW`.
 * - **A backslash is a literal backslash**, never an escape.
 *
 * That last rule is the one that matters here and the reason this is not `shlex`.
 * swe-mux is Windows-first and its arguments are full of paths; a POSIX tokenizer
 * would silently eat every separator in `C:\Users\Jatora\Projects`, and the damage
 * would land in an argument the user never inspected again.
 */

/** Parse a typed command line into argv, using Windows quoting rules. */
export function parseCommandLine(text: string): string[] {
  const args: string[] = []
  let current = ''
  let quoted = false
  let started = false
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]
    if (character === '"') {
      // `""` while already inside quotes is an escaped quote; otherwise the mark
      // just toggles grouping and contributes nothing to the value.
      if (quoted && text[index + 1] === '"') { current += '"'; index += 1 }
      else quoted = !quoted
      started = true
      continue
    }
    if (!quoted && /\s/.test(character)) {
      if (started) { args.push(current); current = ''; started = false }
      continue
    }
    current += character
    started = true
  }
  // An unterminated quote keeps what was typed rather than dropping it: this runs on
  // every keystroke, so half-typed input is the normal case, not an error case.
  if (started) args.push(current)
  return args
}

/** Render argv as a command line that `parseCommandLine` turns back into argv. */
export function formatCommandLine(args: readonly string[]): string {
  return args.map(quoteArgument).join(' ')
}

/** Quote one argument if it needs it, so the round trip is lossless. */
export function quoteArgument(value: string): string {
  if (value === '') return '""'
  if (!/[\s"]/.test(value)) return value
  return `"${value.replace(/"/g, '""')}"`
}

/**
 * The command line a launch profile contributes, for display beside the editor.
 *
 * Deliberately not the whole spawn. The adapter adds the conversation id, the
 * per-session settings file, and the MCP registration on either side of these
 * arguments, and listing those would suggest they are the user's to change when
 * they are exactly the arguments a profile may not set. The caller labels this as
 * partial.
 */
export function launchPreview(
  executable: string,
  harnessArgs: readonly string[],
  profileArgs: readonly string[],
): string {
  return formatCommandLine([executable, ...harnessArgs, ...profileArgs].filter(Boolean))
}
