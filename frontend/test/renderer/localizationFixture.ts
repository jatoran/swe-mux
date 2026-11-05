import english from '../../src/localization/catalogs/en.json' with { type: 'json' }
import { applyLanguagePreference, installCatalog, type Catalog } from '../../src/localization/index.ts'

/** Test-only Chinese catalog. Production intentionally ships an empty Chinese catalog. */
export function installTestChinese() {
  const examples:Record<string,string> = { Language:'语言', General:'常规', Appearance:'外观', Terminals:'终端', Theme:'主题', 'Terminal font':'终端字体', 'Startup folder':'启动文件夹', Renderer:'渲染器' }
  installCatalog('zh-Hans', Object.fromEntries(Object.entries(english).map(([id,text])=>[id,examples[text]||(text.startsWith('Renderer')?text.replace(/^Renderer/,'渲染器'):'〔中〕'+text)])) as Catalog)
}
export function setTestLanguage(language:'en'|'zh-Hans') { applyLanguagePreference(language) }
