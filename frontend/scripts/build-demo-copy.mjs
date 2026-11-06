import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { applyLanguagePreference } from '../src/localization/index.ts'
import { SCENARIOS } from '../src/demo/scenarios.ts'
import { dt } from '../src/demo/messages.ts'

const result = {}
for (const language of ['en', 'zh-Hans']) {
  applyLanguagePreference(language)
  result[language] = {
    title: dt('demo.siteTitle'), desktop: dt('demo.desktop'), phone: dt('demo.phone'), both: dt('demo.both'),
    scenariosLabel: dt('demo.d1a1c36265'), chooseScenario: dt('demo.chooseScenario'),
    scenarioTitle: dt('demo.8565a725ac'), open: dt('demo.open'), openTitle: dt('demo.openTitle'),
    reset: dt('demo.01be30bb4a'), resetTitle: dt('demo.8902d9ed9a'), tryKeys: dt('demo.tryKeys'),
    keyboardHint: dt('demo.keyboardHint'), keymapTitle: dt('demo.1134df1127'), keymapFailure: dt('demo.keymapFailure'),
    disclosure: dt('demo.disclosure'), phoneKeyboard: dt('demo.phoneKeyboard'),
    desktopFrame: dt('demo.desktopFrame'), phoneFrame: dt('demo.phoneFrame'),
    scenarios: SCENARIOS.map(({id, label}) => ({id, label})),
  }
}
writeFileSync(fileURLToPath(new URL('../../site/demo-language.json', import.meta.url)), JSON.stringify(result, null, 2) + '\n')
console.log('Generated bilingual website demo controls from the demo catalogs.')
