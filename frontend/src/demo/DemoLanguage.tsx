import { locale } from '../localization/index.ts'
import { selectDemoLanguage } from './languageControls.ts'
import { dt } from './messages.ts'

export function DemoLanguage() {
  return <label class="demo-language">
    <span>{dt('demo.language')}</span>
    <select data-demo-language aria-label={dt('demo.language')} value={locale.value}
      onChange={event => selectDemoLanguage(event.currentTarget.value)}>
      <option value="en">English</option>
      <option value="zh-Hans">简体中文</option>
    </select>
  </label>
}
