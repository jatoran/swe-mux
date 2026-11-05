import { signal } from '@preact/signals'
import { IntlMessageFormat } from 'intl-messageformat'
import type { ComponentChildren } from 'preact'
import english from './catalogs/en.json' with { type: 'json' }
import chinese from './catalogs/zh-Hans.json' with { type: 'json' }

export type Locale = 'en' | 'zh-Hans'
export type LanguagePreference = '' | Locale
export type MessageId = keyof typeof english
export type TextValues = Record<string, string | number | boolean | Date | null | undefined>
export type RichValues = Record<string, ComponentChildren | Date>
export type Catalog = Partial<Record<MessageId, string>>
const catalogs: Record<Locale, Catalog> = { en: english, 'zh-Hans': chinese }
const CACHE_KEY = 'swe-mux:ui-language'
/** Shared by every render, including memoized leaf components. Never remount the workspace. */
export const locale = signal<Locale>('en')
export const languagePreference = signal<LanguagePreference>('')
export const catalogRevision = signal(0)
const formats = new Map<string, IntlMessageFormat>()
const reported = new Set<string>()
type Diagnostic = { operation: string; locale: Locale; message_id?: string; reason?: string }
let reporter: (entry: Diagnostic) => void = entry => console.warn('localization', entry)

export function setLocalizationReporter(next: typeof reporter): void { reporter = next }
export function resolveLocale(preference: unknown, languages: readonly string[] = []): Locale {
  if (preference === 'en' || preference === 'zh-Hans') return preference
  for (const language of languages) {
    const tag = language.toLowerCase()
    if (tag === 'zh' || tag === 'zh-cn' || tag === 'zh-sg' || tag.startsWith('zh-hans')) return 'zh-Hans'
    if (tag === 'en' || tag.startsWith('en-')) return 'en'
  }
  return 'en'
}
function browserLanguages(): readonly string[] {
  return typeof navigator === 'undefined' ? [] : navigator.languages || [navigator.language]
}
/** An explicit daemon preference always wins over browser detection and the startup cache. */
export function applyLanguagePreference(value: unknown): void {
  const preference: LanguagePreference = value === 'en' || value === 'zh-Hans' ? value : ''
  const next = resolveLocale(preference, browserLanguages())
  languagePreference.value = preference
  if (typeof document !== 'undefined' && document.documentElement) document.documentElement.lang = next
  try { if (typeof localStorage !== 'undefined') localStorage.setItem(CACHE_KEY, preference) } catch { /* storage can be disabled */ }
  if (locale.peek() !== next) {
    locale.value = next
    reporter({ operation: 'language_changed', locale: next })
  }
}
export function initializeLanguage(): void {
  let cached = ''
  try { if (typeof localStorage !== 'undefined') cached = localStorage.getItem(CACHE_KEY) || '' } catch { /* use browser preference */ }
  applyLanguagePreference(cached)
}
function reportOnce(id: string, reason: string): void {
  const key = locale.peek() + ':' + id + ':' + reason
  if (reported.has(key)) return
  reported.add(key)
  reporter({ operation: 'message_fallback', locale: locale.peek(), message_id: id, reason })
}
function formatter(id: MessageId, fallback = false): IntlMessageFormat {
  const current = locale.value
  void catalogRevision.value
  const translated = !fallback && catalogs[current][id]
  const source = translated || english[id]
  if (source === undefined) throw new Error('Unknown message id: ' + id)
  const selected = translated ? current : 'en'
  const key = selected + ':' + id + ':' + source
  let value = formats.get(key)
  if (!value) {
    value = new IntlMessageFormat(source, selected, undefined, { ignoreTag: true })
    formats.set(key, value)
  }
  return value
}
/** Plain messages never interpret HTML. Rich content consists only of caller-owned VNodes. */
export function t(id: MessageId, values?: TextValues): string {
  try { return String(formatter(id).format(values)) }
  catch {
    reportOnce(id, 'invalid_message')
    try { return String(formatter(id, true).format(values)) }
    catch { return english[id] || id }
  }
}
export function rich(id: MessageId, values: RichValues): ComponentChildren {
  try { return formatter(id).format<ComponentChildren>(values as never) }
  catch {
    reportOnce(id, 'invalid_rich_message')
    try { return formatter(id, true).format<ComponentChildren>(values as never) }
    catch { return english[id] || id }
  }
}
/** Install validated catalogs before rendering (also permits isolated renderer fixtures). */
export function hasTranslations(language: Locale): boolean {
  void catalogRevision.value
  return Object.keys(catalogs[language]).length > 0
}

export function installCatalog(language: Locale, catalog: Catalog): void {
  catalogs[language] = language === 'en' ? { ...english, ...catalog } : { ...catalog }
  formats.clear()
  catalogRevision.value++
}
initializeLanguage()

/** Stable English source for structural keys; never use translated text as an identifier. */
export function sourceText(id: string): string {
  const pattern = (english as Record<string, string>)[id]
  if (pattern === undefined) return id
  return String(new IntlMessageFormat(pattern, "en", undefined, {ignoreTag: true}).format())
}


const numberFormats = new Map<string, Intl.NumberFormat>()
const dateFormats = new Map<string, Intl.DateTimeFormat>()

/** These handles stay live even when a module constructs one before the UI mounts. */
export function numberFormatter(options?: Intl.NumberFormatOptions) {
  const current=()=>{
    const key=locale.value+':'+JSON.stringify(options)
    let value=numberFormats.get(key)
    if(!value){value=new Intl.NumberFormat(locale.value,options);numberFormats.set(key,value)}
    return value
  }
  return {
    format:(value:number|bigint)=>current().format(value),
    formatToParts:(value:number|bigint)=>current().formatToParts(value),
    resolvedOptions:()=>current().resolvedOptions(),
  }
}
export function dateTimeFormatter(options?: Intl.DateTimeFormatOptions) {
  const current=()=>{
    const key=locale.value+':'+JSON.stringify(options)
    let value=dateFormats.get(key)
    if(!value){value=new Intl.DateTimeFormat(locale.value,options);dateFormats.set(key,value)}
    return value
  }
  return {
    format:(value?:Date|number)=>current().format(value),
    formatToParts:(value?:Date|number)=>current().formatToParts(value),
    resolvedOptions:()=>current().resolvedOptions(),
  }
}
