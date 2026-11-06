import './languageBootstrap.ts'
import { IntlMessageFormat } from 'intl-messageformat'
import { locale, type TextValues } from '../localization/index.ts'
import english from './catalogs/en.json' with { type: 'json' }
import chinese from './catalogs/zh-Hans.json' with { type: 'json' }
import { demoLog } from './diagnostics.ts'

export type DemoMessageId = keyof typeof english
const catalogs = { en: english, 'zh-Hans': chinese }
const formats = new Map<string, IntlMessageFormat>()
const reported = new Set<string>()

/** Demo copy is separate from product bundles; values are always rendered as text. */
export function dt(id: DemoMessageId, values?: TextValues): string {
  const language = locale.value
  try {
    const key = language + ':' + id
    let formatter = formats.get(key)
    if (!formatter) {
      formatter = new IntlMessageFormat(catalogs[language][id], language, undefined, { ignoreTag: true })
      formats.set(key, formatter)
    }
    return String(formatter.format(values))
  } catch {
    const key = language + ':' + id
    if (!reported.has(key)) { reported.add(key); demoLog('message_fallback', id, language, true) }
    return String(new IntlMessageFormat(english[id], 'en', undefined, { ignoreTag: true }).format(values))
  }
}
