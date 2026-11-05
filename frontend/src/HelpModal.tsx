import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useMemo, useState } from 'preact/hooks'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import {
  HELP_TOPICS, helpDocContent, helpDocsUrl, helpTopic,
  type HelpBlock, type HelpTopic,
} from './helpTopics'

/**
 * The help surface: one modal over the whole topic registry, opened from anywhere.
 *
 * Two things it is deliberately not. It is **not** a second place to configure anything -
 * every control it offers is a link to the one editor that owns it, which is the same rule
 * `SettingLink` carries. And it is **not** hand-written documentation: each topic's body is
 * generated from the feature doc that defines the surface, so the only prose authored here
 * is each topic's one-sentence blurb and the header below.
 *
 * The tour lives at the top because a recovery path nobody can find is not a recovery path,
 * and Settings → General was the only door to it before this existed.
 */
type Props = {
  /** Topic to open on. Unknown or absent lands on the index. */
  initialTopic?: string | null
  onClose: () => void
  onStartTutorial: () => void
  onShowGettingStarted?: () => void
  onRestartSetup?: () => void
  onOpenConfigurator: () => void
  /** Whether the configurator can start here, and why not when it cannot. */
  configurator: { enabled: boolean; reason?: string }
}

const renderBlock = (block: HelpBlock, index: number) => block.kind === 'ul'
  ? <ul key={index}>{block.items.map((item, position) => <li key={position}>{item}</li>)}</ul>
  : <p key={index}>{block.text}</p>

function TopicBody({ topic }: { topic: HelpTopic }) {
  const content = helpDocContent(topic.id)
  return <div class="help-topic">
    <h3>{topic.title}</h3>
    <p class="help-blurb">{topic.blurb}</p>
    {/* Said out loud rather than implied: the text below is the design document, not a
        summary of it, which is what makes it impossible for the two to disagree. */}
    {content && <>
      <p class="help-source">{rich("HelpModal.8eabc7257f", { value1: <code>{content.doc}</code> })}</p>
      {content.sections.map(section => <section key={section.heading} class="help-doc-section">
        <h4>{section.heading}</h4>
        {section.blocks.map(renderBlock)}
      </section>)}
    </>}
    <p class="help-more">
      <a href={helpDocsUrl(topic)} target="_blank" rel="noreferrer noopener">{t("HelpModal.cbd27402a9")}</a>
    </p>
  </div>
}

export function HelpModal({ initialTopic, onClose, onStartTutorial, onShowGettingStarted, onRestartSetup, onOpenConfigurator, configurator }: Props) {
  const [selected, setSelected] = useState<string | null>(initialTopic && helpTopic(initialTopic) ? initialTopic : null)
  const [query, setQuery] = useState('')
  // Re-open on a different topic without unmounting: the command bus can ask for one while
  // the modal is already up, and remounting would throw away the reader's scroll for no
  // reason. `null` (the plain `help.open`) leaves the selection alone for the same reason.
  useEffect(() => { if (initialTopic && helpTopic(initialTopic)) setSelected(initialTopic) }, [initialTopic])

  useDismissLevel(onClose, true, 'help')
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); dismissStack.pop() } }
    window.addEventListener('keydown', key, true)
    return () => window.removeEventListener('keydown', key, true)
  }, [])

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return HELP_TOPICS
    return HELP_TOPICS.filter(topic => `${topic.title} ${topic.blurb} ${topic.id}`.toLowerCase().includes(needle))
  }, [query, uiLocale.value, catalogRevision.value])
  const topic = selected ? helpTopic(selected) : null

  return <div class="modal-layer help-layer" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section class={`modal help-modal${topic ? ' topic-open' : ''}`} role="dialog" aria-modal="true" aria-label={t("HelpModal.b79cac926e")}>
      <div class="modal-heading">
        <div><span>{t("HelpModal.37a6760fa4")}</span><h2>{topic ? topic.title : t("HelpModal.ff49260423")}</h2></div>
        <button type="button" aria-label={t("HelpModal.88f2b69280")} onClick={onClose}>×</button>
      </div>
      <div class="help-body">
        <nav class="help-nav" aria-label={t("HelpModal.138dc09b28")}>
          {/* The tour, first and unconditional. It is the only thing here that *does*
              something rather than explaining something, and it was previously reachable
              only from one section of one Settings tab. */}
          <button type="button" class="help-action primary" onClick={onStartTutorial}>{t("HelpModal.87ad9af10e")}</button>
          {onShowGettingStarted && <button type="button" class="help-action" onClick={onShowGettingStarted}>{t("HelpModal.144cb9d488")}</button>}
          {onRestartSetup && <button type="button" class="help-action" onClick={onRestartSetup}>{t("HelpModal.fa1896c897")}</button>}
          <button
            type="button"
            class="help-action"
            disabled={!configurator.enabled}
            title={configurator.enabled ? t("HelpModal.c42e9256a7") : configurator.reason}
            onClick={onOpenConfigurator}
          >{t("HelpModal.9e252559e7")}</button>
          <label class="help-search"><span class="sr-only">{t("HelpModal.82a9d88cd5")}</span>
            <input type="search" placeholder={t("HelpModal.4fae04c104")} value={query} onInput={event => setQuery(event.currentTarget.value)} />
          </label>
          {selected && <button type="button" class="help-topic-link" onClick={() => setSelected(null)}>{t("HelpModal.6e435c329d")}</button>}
          <ul class="help-topic-list">
            {matches.map(item => <li key={item.id}>
              <button
                type="button"
                class={`help-topic-link${item.id === selected ? ' active' : ''}`}
                aria-current={item.id === selected}
                onClick={() => setSelected(item.id)}
              >{item.title}</button>
            </li>)}
            {!matches.length && <li><p class="help-empty">{t("HelpModal.c71310b721")}</p></li>}
          </ul>
        </nav>
        <div class="help-content">
          {topic ? <TopicBody topic={topic} /> : <div class="help-index">
                <p>{t("HelpModal.ca360c9583")}</p>
                {matches.map(item => <button key={item.id} type="button" class="help-index-row" onClick={() => setSelected(item.id)}>
                  <strong>{item.title}</strong><span>{item.blurb}</span>
                </button>)}
                {!matches.length && <p class="help-empty">{t("HelpModal.c71310b721")}</p>}
              </div>}
        </div>
      </div>
    </section>
  </div>
}
