import assert from 'node:assert/strict'
import test from 'node:test'
import { h } from 'preact'
import { IntlMessageFormat } from 'intl-messageformat'
import english from '../src/localization/catalogs/en.json' with { type: 'json' }
import { applyLanguagePreference, dateTimeFormatter, numberFormatter, installCatalog, locale, resolveLocale, rich, setLocalizationReporter, t, type MessageId } from '../src/localization/index.ts'

setLocalizationReporter(() => {})

test('language selection uses supported browser preferences and an explicit selection wins', () => {
  assert.equal(resolveLocale('', ['zh-CN', 'en-US']), 'zh-Hans')
  assert.equal(resolveLocale('', ['en-GB', 'zh-CN']), 'en')
  assert.equal(resolveLocale('', ['fr', 'zh-SG']), 'zh-Hans')
  assert.equal(resolveLocale('', ['zh-TW']), 'en')
  assert.equal(resolveLocale('en', ['zh-CN']), 'en')
  assert.equal(resolveLocale('zh-Hans', ['en-US']), 'zh-Hans')
  assert.equal(resolveLocale('fr', ['en-US']), 'en')
})

test('every English message is valid ICU, including braces and quoted technical examples', () => {
  for (const [id, text] of Object.entries(english)) {
    assert.doesNotThrow(() => new IntlMessageFormat(text, 'en', undefined, { ignoreTag: true }), id)
  }
})

test('switching language uses the same message ids and missing translations fall back to English', () => {
  const id = 'language.label'
  installCatalog('zh-Hans', { [id]: '语言' })
  applyLanguagePreference('en')
  assert.equal(t(id), 'Language')
  applyLanguagePreference('zh-Hans')
  assert.equal(t(id), '语言')
  assert.equal(t('language.note'), english['language.note'])
  applyLanguagePreference('en')
  assert.equal(t(id), 'Language')
  installCatalog('zh-Hans', {})
})

test('catalog replacement invalidates formatters without requiring a reload', () => {
  applyLanguagePreference('zh-Hans')
  installCatalog('zh-Hans', { 'language.label': '第一' })
  assert.equal(t('language.label'), '第一')
  installCatalog('zh-Hans', { 'language.label': '第二' })
  assert.equal(t('language.label'), '第二')
  applyLanguagePreference('en')
  installCatalog('zh-Hans', {})
})

test('malformed translations report a bounded diagnostic and fall back to the source', () => {
  const diagnostics: unknown[] = []
  setLocalizationReporter(entry => diagnostics.push(entry))
  installCatalog('zh-Hans', { 'language.label': '{broken' })
  applyLanguagePreference('zh-Hans')
  assert.equal(t('language.label'), 'Language')
  assert.equal(t('language.label'), 'Language')
  assert.equal(diagnostics.filter((entry: any) => entry.operation === 'message_fallback').length, 1)
  applyLanguagePreference('en')
  installCatalog('zh-Hans', {})
  setLocalizationReporter(() => {})
})

test('ICU supports Chinese plural rules and named values can be reordered safely', () => {
  const id = 'language.label' as MessageId
  installCatalog('zh-Hans', { [id]: '{count, plural, other {共 # 个项目}}：{name}' })
  applyLanguagePreference('zh-Hans')
  assert.equal(t(id, { count: 2, name: '<script>alert(1)</script>' }), '共 2 个项目：<script>alert(1)</script>')
  assert.equal(t(id, { count: 1, name: '测试' }), '共 1 个项目：测试')
  installCatalog('zh-Hans', { [id]: '{link}：{name}' })
  const link = h('a', { href: '/local' }, 'Open')
  const result = rich(id, { name: 'Project', link }) as unknown[]
  assert.equal(result[0], link)
  assert.equal(result[1], '：Project')
  applyLanguagePreference('en')
  installCatalog('zh-Hans', {})
})

test('a no-op language update does not notify subscribed views', () => {
  applyLanguagePreference('en')
  let updates = 0
  const unsubscribe = locale.subscribe(() => updates++)
  const initial = updates
  applyLanguagePreference('en')
  assert.equal(updates, initial)
  applyLanguagePreference('zh-Hans')
  assert.equal(updates, initial + 1)
  unsubscribe()
  applyLanguagePreference('en')
})


test('formatters created before selection follow the selected language', () => {
  const numbers = numberFormatter({notation:'compact',maximumFractionDigits:1})
  const dates = dateTimeFormatter({month:'long',timeZone:'UTC'})
  applyLanguagePreference('en')
  assert.equal(numbers.format(12345), new Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(12345))
  assert.equal(dates.format(new Date('2026-01-01T00:00:00Z')), 'January')
  applyLanguagePreference('zh-Hans')
  assert.equal(numbers.format(12345), new Intl.NumberFormat('zh-Hans',{notation:'compact',maximumFractionDigits:1}).format(12345))
  assert.equal(dates.format(new Date('2026-01-01T00:00:00Z')), '一月')
  applyLanguagePreference('en')
})
