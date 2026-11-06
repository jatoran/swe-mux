import { applyLanguagePreference, locale, type Locale } from '../localization/index.ts'
import { apply, onMutation, state } from './store.ts'
import { requestedDemoLanguage } from './languageBootstrap.ts'
import { demoLog } from './diagnostics.ts'
import { dismissResume, stop } from './director.ts'

export const DEMO_LANGUAGE_EVENT = 'swemux-demo:language-changed'

export function selectDemoLanguage(value: string): void {
  if (value !== 'en' && value !== 'zh-Hans') return
  apply({ kind: 'config-patch', patch: { ui_language: value } })
}

export function installDemoLanguageControls(): void {
  if (requestedDemoLanguage) state.config.ui_language = requestedDemoLanguage
  applyLanguagePreference(state.config.ui_language)
  const publish = () => {
    const before = locale.peek()
    applyLanguagePreference(state.config.ui_language)
    const language: Locale = locale.peek()
    if (before !== language) { stop('dismissed'); dismissResume() }
    const url = new URL(location.href)
    url.searchParams.set('lang', language)
    history.replaceState(null, '', url)
    window.dispatchEvent(new CustomEvent(DEMO_LANGUAGE_EVENT, { detail: language }))
    demoLog('language_selected', language)
  }
  onMutation(mutation => {
    if (mutation.kind === 'config-patch' && 'ui_language' in mutation.patch) publish()
  })
  window.addEventListener('swemux-demo:select-language', event => {
    selectDemoLanguage(String((event as CustomEvent).detail))
  })
  publish()
}
