import chinese from '../../src/localization/catalogs/zh-Hans.json' with { type: 'json' }
import { applyLanguagePreference, installCatalog } from '../../src/localization/index.ts'

/** Exercise the shipped Chinese catalog in the isolated renderer harness. */
export function installTestChinese() {
  installCatalog('zh-Hans', chinese)
}
export function setTestLanguage(language: 'en' | 'zh-Hans') { applyLanguagePreference(language) }
