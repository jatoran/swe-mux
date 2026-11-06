import { locale } from '../localization/index.ts'
import { KEYMAP_FIXTURE } from './keymapFixture.ts'
import translations from './keymapTranslations.json' with { type: 'json' }

let chinese: typeof KEYMAP_FIXTURE | undefined

/** Generated paths touch only UiMessage presentation fields, never bindings or IDs. */
export function localizedKeymapFixture(): typeof KEYMAP_FIXTURE {
  if (locale.value === 'en') return KEYMAP_FIXTURE
  if (!chinese) {
    chinese = structuredClone(KEYMAP_FIXTURE)
    for (const { path, value } of translations) {
      let target: unknown = chinese
      for (const key of path.slice(0, -1)) target = (target as Record<string, unknown>)[key]
      ;(target as Record<string, unknown>)[path[path.length - 1]] = value
    }
  }
  return chinese
}
