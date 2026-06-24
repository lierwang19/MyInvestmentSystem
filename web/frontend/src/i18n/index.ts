/**
 * i18n configuration and setup.
 *
 * Usage in components:
 *   import { useI18n } from 'vue-i18n'
 *   const { t } = useI18n()
 *   // In template: {{ t('common.save') }}
 */
import { createI18n } from 'vue-i18n'
import zhCN from './zh-CN'
import en from './en'

const savedLocale = localStorage.getItem('invest-locale') || 'zh-CN'

const i18n = createI18n({
    legacy: false,
    locale: savedLocale,
    fallbackLocale: 'zh-CN',
    messages: {
        'zh-CN': zhCN,
        'en': en,
    },
})

export default i18n

/**
 * Switch locale at runtime and persist choice.
 */
export function setLocale(locale: 'zh-CN' | 'en') {
    i18n.global.locale.value = locale
    localStorage.setItem('invest-locale', locale)
}
