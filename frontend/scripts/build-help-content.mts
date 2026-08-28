// Generates `src/helpContent.generated.ts` from the feature docs that define the surfaces.
//
// The point of generating rather than writing is the one the phase names: in-app help must
// not be able to drift from the design document that defines the surface. A hand-written
// paragraph beside a doc is a second copy of the doc, and the copy is what rots - the same
// argument the configurator's generated inventory carries, and the same argument behind
// `harnessRegistrySeed.ts`.
//
// It is generated *into the tree* rather than imported at build time on purpose. `.docs/`
// is not carried in the wheel or the PyInstaller bundle, and the node test runner resolves
// no `?raw` specifier, so a live import would be unbuildable from a wheel and untestable
// from the unit suite. A committed generated file is buildable everywhere and its freshness
// is a test (`test/helpTopics.test.ts`), which is the same trade `harnessRegistrySeed.ts`
// already makes.
//
// Regenerate: node frontend/scripts/build-help-content.mts
// The test fails when the committed file no longer matches the docs.

import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export const HELP_SOURCE_ROOT = ['.docs', 'design', 'features']

// Declared here rather than imported from `src/helpTopics.ts`, which imports the file this
// writes: a build script that depends on its own output is a bootstrap problem waiting to
// happen. `helpTopics.ts` owns the shipped shape and the generated file is typed against
// it, so the two are checked against each other by `tsc` rather than by convention.
export type HelpBlock = { kind: 'p'; text: string } | { kind: 'ul'; items: string[] }

/**
 * What each topic pulls out of its doc, in the order a reader wants it.
 *
 * Headings are matched exactly, so renaming one in the doc fails the freshness test rather
 * than silently emptying a help modal. Keep the list short: this is the first two minutes
 * of a surface, not its specification, and the modal links to the full page for the rest.
 */
export const HELP_SECTIONS = [
  // The scan timeline carries a second section because its gating is the thing people
  // actually arrive asking about: three independent switches, and an empty pane looks the
  // same whichever one is closed.
  { topic: 'scan-timeline', doc: 'scan-timeline.md', headings: ['What it is', 'Authorization and lifetime'] },
  { topic: 'git', doc: 'git.md', headings: ['What it is'] },
  { topic: 'prompt-queue', doc: 'prompt-queue.md', headings: ['What it is'] },
  { topic: 'scheduled-runs', doc: 'scheduled-runs.md', headings: ['What it is'] },
  { topic: 'attention-ranking', doc: 'attention-ranking.md', headings: ['What it is'] },
  { topic: 'agent-environment', doc: 'agent-environment.md', headings: ['Purpose'] },
  { topic: 'processes-and-previews', doc: 'processes-and-previews.md', headings: ['What it is'] },
  { topic: 'project-resources', doc: 'project-resources.md', headings: ['What it is'] },
  { topic: 'transcript-branches', doc: 'transcript-branches.md', headings: ['What it is'] },
  { topic: 'project-actions', doc: 'project-actions.md', headings: ['What it is'] },
]

const inline = (text: string): string => text
  .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
  // A link's label is the sentence; its target is a repository path a reader in a modal
  // cannot follow. Keeping the label and dropping the target is the only lossless half.
  .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  .replace(/`([^`]+)`/g, '$1')
  .replace(/\*\*([^*]+)\*\*/g, '$1')
  .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '$1')
  .replace(/\s+/g, ' ')
  .trim()

/** The lines of one `## ` section, exclusive of its own heading. Empty when absent. */
export function sectionLines(markdown: string, heading: string): string[] {
  const lines = markdown.split(/\r?\n/)
  const start = lines.findIndex(line => line.trim() === `## ${heading}`)
  if (start < 0) return []
  const rest = lines.slice(start + 1)
  const end = rest.findIndex(line => /^##\s/.test(line))
  return end < 0 ? rest : rest.slice(0, end)
}

/**
 * One section as renderable blocks.
 *
 * Two block kinds only. Everything a feature doc's opening section contains is a paragraph
 * or a list, and a third kind would be a markdown renderer - which is the wrong thing to
 * put in a help modal that has to stay legible on a phone.
 */
export function helpBlocks(markdown: string, heading: string): HelpBlock[] {
  const lines = sectionLines(markdown, heading)
  const blocks: HelpBlock[] = []
  let paragraph: string[] = []
  let items: string[] | null = null
  const flushParagraph = () => {
    if (!paragraph.length) return
    const text = inline(paragraph.join(' '))
    if (text) blocks.push({ kind: 'p', text })
    paragraph = []
  }
  const flushItems = () => {
    if (items && items.length) blocks.push({ kind: 'ul', items })
    items = null
  }
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) { flushParagraph(); flushItems(); continue }
    const bullet = line.match(/^(?:[-*]|\d+\.)\s+(.*)$/)
    if (bullet) {
      flushParagraph()
      items = items || []
      const text = inline(bullet[1])
      if (text) items.push(text)
      continue
    }
    // A continuation line under a bullet belongs to that bullet, not to a new paragraph:
    // these docs wrap one sentence per line and indent the rest of a list item.
    if (items && /^\s/.test(raw)) { items[items.length - 1] = `${items[items.length - 1]} ${inline(line)}`; continue }
    flushItems()
    paragraph.push(line)
  }
  flushParagraph()
  flushItems()
  return blocks
}

/** The whole generated module, as text. Pure, so the test can compare without writing. */
export function renderHelpContent(root: string): string {
  const sections = HELP_SECTIONS.map(entry => {
    const markdown = readFileSync(join(root, ...HELP_SOURCE_ROOT, entry.doc), 'utf8')
    return {
      topic: entry.topic,
      doc: `${HELP_SOURCE_ROOT.join('/')}/${entry.doc}`,
      sections: entry.headings.map(heading => ({ heading, blocks: helpBlocks(markdown, heading) })),
    }
  })
  return [
    '// GENERATED FILE - do not edit by hand.',
    '// Regenerate: node frontend/scripts/build-help-content.mts',
    `// Source of truth: ${HELP_SOURCE_ROOT.join('/')}/ (see HELP_SECTIONS in that script).`,
    '// frontend/test/helpTopics.test.ts fails when this file drifts from those docs.',
    "import type { HelpDocContent } from './helpTopics.ts'",
    '',
    `export const HELP_DOC_CONTENT: HelpDocContent[] = ${JSON.stringify(sections, null, 2)}`,
    '',
  ].join('\n')
}

// Run as a script, not imported by a test. `basename` rather than the whole path because
// the two spellings differ on Windows (a `file://` URL against a backslash argv).
const invoked = (process.argv[1] || '').replace(/\\/g, '/').split('/').pop() || ''
if (invoked && import.meta.url.endsWith(invoked)) {
  const root = join(import.meta.dirname, '..', '..')
  writeFileSync(join(root, 'frontend', 'src', 'helpContent.generated.ts'), renderHelpContent(root), 'utf8')
  process.stdout.write('wrote frontend/src/helpContent.generated.ts\n')
}
