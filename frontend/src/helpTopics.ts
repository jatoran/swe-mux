import { t } from './localization/index.ts'
/**
 * The in-app help surface: which topics exist, where each is reached from, and where the
 * text of each one comes from.
 *
 * Two rules the registry exists to enforce, and neither is optional.
 *
 * **Nobody writes help prose here.** A topic's body is generated from the feature doc that
 * defines the surface (`helpContent.generated.ts`, written by
 * `scripts/build-help-content.mts`), so the help cannot drift from the design document the
 * way a hand-copied paragraph does. What a topic *may* carry by hand is one `blurb` -
 * a plain sentence for a reader who has not read a design document and should not have to.
 * The blurb says what the surface is for; the generated body says what it is.
 *
 * **A topic reached only by clicking has no palette entry and no voice phrase**, which is
 * the same argument `drawerSegments.ts` carries for segments. So help is a registered
 * command (`help.open`, plus one per topic), the tour is re-openable from it, and the
 * in-context control on a drawer tab is a second door onto the same registry rather than a
 * surface of its own.
 *
 * Pure and JSX-free, so `test/helpTopics.test.ts` can assert every claim in it - that each
 * doc and heading still exists, that each anchor names a live drawer tab and segment, and
 * that the generated file still matches the docs - without mounting anything.
 */

// Explicit extensions: the node test runner imports this module directly and resolves no
// bare specifiers, the same reason `dismissStack.ts` and `settingReveal.ts` are spelled out.
import { DRAWER_TABS, type DrawerTabId } from './drawerTabs.ts'
import { drawerSegment } from './drawerSegments.ts'
import { HELP_DOC_CONTENT } from './helpContent.generated.ts'

export type HelpBlock = { kind: 'p'; text: string } | { kind: 'ul'; items: string[] }
export type HelpDocSection = { heading: string; blocks: HelpBlock[] }
export type HelpDocContent = { topic: string; doc: string; sections: HelpDocSection[] }

export type HelpTopic = {
  /** Stable id. Part of the command id, so treat it as permanent. */
  id: string
  title: string
  /** One authored sentence. The only prose in this file, and deliberately so. */
  blurb: string
  /** Where the in-context control appears. Absent for a topic with no drawer home. */
  anchor?: { tab: DrawerTabId; segment?: string }
  /**
   * The published documentation page this topic continues on.
   *
   * A *site* slug, not this topic's id, and the two are deliberately different vocabularies:
   * `site/tools/docs_content.py` owns twenty-two reader-facing pages while this registry is
   * keyed by the feature doc that generated the body, so several topics share one page.
   * Deriving the URL from the id would have shipped nine links straight to a 404, which is
   * exactly the dead end this phase exists to remove - so the mapping is explicit and
   * `test/helpTopics.test.ts` checks every slug against the site's own page list.
   */
  docs: string
}

/**
 * `https://swemux.dev/docs/<slug>/` is one page, and the trailing slash is load-bearing
 * under GitHub Pages' Actions source. The fragment form (`/docs/#slug`) was retired with the
 * docs browser and must not come back here (`site/README.md`).
 */
export const DOCS_BASE = 'https://swemux.dev/docs/'

export const HELP_TOPICS: HelpTopic[] = [
  {
    id: 'scan-timeline',
    get title() { return t("helpTopics.446eb6eb25") },
    get blurb() { return t("helpTopics.6f76fd98b8") },
    anchor: { tab: 'activity', segment: 'timeline' },
    docs: 'control-plane',
  },
  {
    id: 'prompt-queue',
    get title() { return t("helpTopics.ac87afaf2f") },
    get blurb() { return t("helpTopics.9bfb14a712") },
    anchor: { tab: 'queue' },
    docs: 'queue',
  },
  {
    id: 'git',
    get title() { return t("helpTopics.71b6da4552") },
    get blurb() { return t("helpTopics.9770bbb9f9") },
    anchor: { tab: 'git' },
    docs: 'git',
  },
  {
    id: 'scheduled-runs',
    get title() { return t("helpTopics.19110a1000") },
    get blurb() { return t("helpTopics.442fe00454") },
    anchor: { tab: 'schedule' },
    docs: 'automation',
  },
  {
    id: 'attention-ranking',
    get title() { return t("helpTopics.8e3d843367") },
    get blurb() { return t("helpTopics.69aa826255") },
    anchor: { tab: 'notifications' },
    docs: 'automation',
  },
  {
    id: 'agent-environment',
    get title() { return t("helpTopics.1774cca7f6") },
    get blurb() { return t("helpTopics.86542b3f92") },
    anchor: { tab: 'agent' },
    docs: 'sessions',
  },
  {
    id: 'processes-and-previews',
    get title() { return t("helpTopics.9002e23562") },
    get blurb() { return t("helpTopics.eeebad8445") },
    anchor: { tab: 'processes' },
    docs: 'notes-files',
  },
  {
    id: 'project-resources',
    get title() { return t("helpTopics.471fff1131") },
    get blurb() { return t("helpTopics.0c11a7d783") },
    anchor: { tab: 'files' },
    docs: 'notes-files',
  },
  {
    id: 'transcript-branches',
    get title() { return t("helpTopics.721164f0dc") },
    get blurb() { return t("helpTopics.4488883b2e") },
    anchor: { tab: 'transcript' },
    docs: 'history',
  },
  {
    id: 'project-actions',
    get title() { return t("helpTopics.7590a4b8ee") },
    get blurb() { return t("helpTopics.d6c053584c") },
    anchor: { tab: 'actions' },
    docs: 'sessions',
  },
  {
    // No `anchor`: the keyboard is not a drawer tab, and this is the one topic
    // somebody looks for before they know where anything is.
    id: 'keybindings',
    get title() { return t("heading.e9bef0b0f3") },
    get blurb() { return t("helpTopics.e5e0806895") },
    docs: 'keyboard',
  },
]

export const helpTopic = (id: string): HelpTopic | null => HELP_TOPICS.find(topic => topic.id === id) ?? null

export const helpDocContent = (id: string): HelpDocContent | null =>
  HELP_DOC_CONTENT.find(entry => entry.topic === id) ?? null

/** The topic an open drawer tab offers help for, given whichever segment it is showing. */
export function helpTopicForDrawer(tab: DrawerTabId, segment: string | null): HelpTopic | null {
  const exact = HELP_TOPICS.find(topic => topic.anchor?.tab === tab && topic.anchor.segment === (segment ?? undefined))
  if (exact) return exact
  // A tab-wide topic answers for every segment that has none of its own, which is what
  // keeps Git's three readings from each needing a row here.
  return HELP_TOPICS.find(topic => topic.anchor?.tab === tab && !topic.anchor.segment) ?? null
}

/** The command id one topic answers to. One rule, used by the registry and the palette. */
export const helpCommandId = (id: string): string => `help.topic.${id}`

/** The published page one topic continues on. Takes the topic, never its id. */
export const helpDocsUrl = (topic: HelpTopic): string => `${DOCS_BASE}${topic.docs}/`

/** Which drawer tabs currently offer help, for the test that says so out loud. */
export const helpAnchoredTabs = (): DrawerTabId[] =>
  DRAWER_TABS.filter(tab => HELP_TOPICS.some(topic => topic.anchor?.tab === tab.id)).map(tab => tab.id)

/** Whether a topic's declared anchor still names a live tab and, if given, a live segment. */
export function helpAnchorResolves(topic: HelpTopic): boolean {
  if (!topic.anchor) return true
  if (!DRAWER_TABS.some(tab => tab.id === topic.anchor!.tab)) return false
  if (!topic.anchor.segment) return true
  return drawerSegment(topic.anchor.tab, topic.anchor.segment) !== null
}
