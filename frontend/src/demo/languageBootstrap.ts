import { applyLanguagePreference, locale, type Locale } from '../localization/index.ts'

/** URL language wins before authored fixtures are constructed. No locale-specific build. */
export const requestedDemoLanguage: Locale | undefined = (() => {
  if (typeof location === 'undefined') return undefined
  const value = new URLSearchParams(location.search).get('lang')
  return value === 'en' || value === 'zh-Hans' ? value : undefined
})()
if (requestedDemoLanguage) applyLanguagePreference(requestedDemoLanguage)
export const initialDemoLanguage = locale.peek()
