import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  DOCS_BASE, HELP_TOPICS, helpAnchorResolves, helpCommandId, helpDocContent,
  helpDocsUrl, helpTopic, helpTopicForDrawer,
} from '../src/helpTopics.ts'
import { DRAWER_TABS } from '../src/drawerTabs.ts'
// The generator is the shared implementation, imported rather than reimplemented: a second
// copy of the extractor here would be able to agree with itself while disagreeing with
// what actually ships.
import { HELP_SECTIONS, HELP_SOURCE_ROOT, helpBlocks, renderHelpContent, sectionLines } from '../scripts/build-help-content.mts'

const repoRoot = join(import.meta.dirname, '..', '..')
const docPath = (name: string) => join(repoRoot, ...HELP_SOURCE_ROOT, name)

test('every help topic names a feature doc that exists', () => {
  for (const entry of HELP_SECTIONS) {
    assert.ok(existsSync(docPath(entry.doc)), `${entry.topic} names a doc that does not exist: ${entry.doc}`)
  }
})

test('every heading a topic pulls still exists in its doc, and is not empty', () => {
  // The failure this catches is silent by construction: a renamed `##` yields no lines,
  // the generator writes an empty block list, and the modal renders a heading over
  // nothing. Empty is the wrong shape for "the doc moved", so it fails here instead.
  for (const entry of HELP_SECTIONS) {
    const markdown = readFileSync(docPath(entry.doc), 'utf8')
    for (const heading of entry.headings) {
      assert.ok(
        sectionLines(markdown, heading).length > 0,
        `${entry.doc} no longer has a "## ${heading}" section, which ${entry.topic} pulls`,
      )
      assert.ok(
        helpBlocks(markdown, heading).length > 0,
        `"## ${heading}" in ${entry.doc} produced no renderable text`,
      )
    }
  }
})

test('the generated help content is what the docs say right now', () => {
  // The freshness half, and the reason the content may be generated into the tree at all.
  // `.docs/` is carried in neither the wheel nor the PyInstaller bundle, so the shipped
  // copy has to be committed - which means the only thing standing between it and the
  // docs is this comparison.
  const committed = readFileSync(join(repoRoot, 'frontend', 'src', 'helpContent.generated.ts'), 'utf8')
  assert.equal(
    committed.replace(/\r\n/g, '\n'),
    renderHelpContent(repoRoot).replace(/\r\n/g, '\n'),
    'helpContent.generated.ts is stale - run: node frontend/scripts/build-help-content.mts',
  )
})

test('every topic has generated content, and every generated entry has a topic', () => {
  for (const topic of HELP_TOPICS) {
    const content = helpDocContent(topic.id)
    assert.ok(content, `${topic.id} has no generated content; add it to HELP_SECTIONS`)
    assert.ok(content!.sections.length > 0, `${topic.id} generated no sections`)
  }
  for (const entry of HELP_SECTIONS) {
    assert.ok(helpTopic(entry.topic), `${entry.topic} is generated but is not a registered help topic`)
  }
})

test('every topic carries an authored blurb as well as the doc text', () => {
  // The doc is written for whoever implements the surface. The blurb is the one sentence
  // written for whoever just opened it, and a topic without one is a design document
  // presented as help.
  for (const topic of HELP_TOPICS) {
    assert.ok(topic.title.trim(), `${topic.id} has no title`)
    assert.ok(topic.blurb.trim().length > 40, `${topic.id} needs a real blurb, not a label`)
  }
})

test('every topic anchor names a drawer tab and segment that still exist', () => {
  for (const topic of HELP_TOPICS) {
    assert.ok(helpAnchorResolves(topic), `${topic.id} is anchored on chrome that no longer exists`)
  }
})

test('an anchored drawer tab offers its topic for every segment it can show', () => {
  // The in-context control reads the registry rather than a per-tab condition, so a tab
  // with a topic must answer for each of its segments - otherwise the "?" appears and
  // disappears as the reader switches segments, which reads as a bug rather than a rule.
  for (const topic of HELP_TOPICS) {
    if (!topic.anchor || topic.anchor.segment) continue
    assert.equal(helpTopicForDrawer(topic.anchor.tab, null)?.id, topic.id)
    assert.equal(helpTopicForDrawer(topic.anchor.tab, 'anything')?.id, topic.id)
  }
})

test('the scan timeline is reachable from the tab that draws it', () => {
  // Phase 16's exit criterion names this one specifically: the scan timeline's help must
  // open from the tab. It is Activity's Timeline segment, not a tab of its own.
  const topic = helpTopicForDrawer('activity', 'timeline')
  assert.equal(topic?.id, 'scan-timeline')
  const content = helpDocContent('scan-timeline')
  assert.equal(content?.doc, '.docs/design/features/scan-timeline.md')
  // The gating explanation is the half people actually arrive asking about, so it is
  // asserted present rather than left to whichever headings the registry happens to pull.
  assert.ok(content?.sections.some(section => section.heading === 'Authorization and lifetime'))
})

test('a tab with no registered topic offers no help control', () => {
  const anchored = new Set(HELP_TOPICS.map(topic => topic.anchor?.tab))
  const unanchored = DRAWER_TABS.filter(tab => !anchored.has(tab.id))
  for (const tab of unanchored) {
    assert.equal(helpTopicForDrawer(tab.id, null), null, `${tab.id} has no topic but is offered one`)
  }
})

test('command ids and docs URLs are one rule each', () => {
  assert.equal(helpCommandId('scan-timeline'), 'help.topic.scan-timeline')
  const git = helpTopic('git')!
  // Trailing slash: under the Actions Pages source `/docs/<slug>` does not resolve, and
  // the retired `/docs/#<slug>` fragment form must not come back (`site/README.md`).
  assert.equal(helpDocsUrl(git), `${DOCS_BASE}git/`)
  for (const topic of HELP_TOPICS) {
    assert.ok(helpDocsUrl(topic).endsWith('/'))
    assert.ok(!helpDocsUrl(topic).includes('#'))
  }
})

test('every topic links to a documentation page the site actually publishes', () => {
  // The reason this is checked rather than derived. A topic is keyed by the feature doc that
  // generated its body, and the site is keyed by twenty-two reader-facing pages; deriving
  // `/docs/<topic.id>/` would have shipped nine links to a 404, which is the same dead end
  // as the assistant's "Settings → Assistant". The site owns the list, so the site is asked.
  const site = readFileSync(join(repoRoot, 'site', 'tools', 'docs_content.py'), 'utf8')
  const slugs = new Set([...site.matchAll(/^\s*slug="([a-z0-9-]+)",\s*$/gm)].map(match => match[1]))
  assert.ok(slugs.size > 10, 'expected to find the site page list; the declaration shape moved')
  for (const topic of HELP_TOPICS) {
    assert.ok(slugs.has(topic.docs), `${topic.id} links to /docs/${topic.docs}/, which the site does not publish`)
  }
})
