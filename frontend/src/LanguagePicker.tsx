import { useState } from 'preact/hooks'
import { api } from './api'
import { applyLanguagePreference, hasTranslations, locale, t, type Locale } from './localization/index.ts'

/** Saves independently of a Settings draft, so setup and every connected device share it. */
export function LanguagePicker({onSaved}:{onSaved?:(config:{ui_language:Locale;revision:number})=>void}) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const change = async (language: Locale) => {
    setBusy(true); setFailed(false)
    try {
      const config = await api<{ ui_language: Locale; revision: number }>('PATCH', '/api/config', { ui_language: language }, { timeoutMs: 10000 })
      applyLanguagePreference(config.ui_language)
      onSaved?.(config)
    } catch { setFailed(true) }
    finally { setBusy(false) }
  }
  return <div class="language-picker">
    <label>{t('language.label')}<select aria-label={t('language.label')} value={locale.value} disabled={busy}
      onChange={event => void change(event.currentTarget.value as Locale)}>
      <option value="en" lang="en">English</option>
      <option value="zh-Hans" lang="zh-Hans">简体中文</option>
    </select></label>
    <p>{t('language.note')}</p>
    {locale.value === 'zh-Hans' && !hasTranslations('zh-Hans') && <p role="status">{t('language.pending')}</p>}
    {failed && <p role="alert">{t('language.saveError')}</p>}
  </div>
}
