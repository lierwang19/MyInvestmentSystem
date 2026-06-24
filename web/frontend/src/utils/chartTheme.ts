/**
 * Shared ECharts theme utilities — reads CSS variables for consistent styling
 * across MarketOverview and BacktestCenter charts.
 */

const getCssVar = (name: string): string =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()

export function getChartColors() {
  return {
    textColor:    getCssVar('--text-color')     || '#f5f5f7',
    mutedColor:   getCssVar('--text-secondary') || '#86868b',
    borderColor:  getCssVar('--border-color')   || 'rgba(255,255,255,0.08)',
    cardBg:       getCssVar('--card-bg')        || 'rgba(28,28,30,0.75)',
    primaryColor: getCssVar('--primary-color')  || '#0a84ff',
    successColor: getCssVar('--success-color')  || '#30d158',
    warningColor: getCssVar('--warning-color')  || '#ffd60a',
    dangerColor:  getCssVar('--danger-color')   || '#ff453a',
  }
}

export function buildAxisCommon() {
  const { mutedColor, borderColor } = getChartColors()
  return {
    axisLine:  { lineStyle: { color: borderColor } },
    axisTick:  { lineStyle: { color: borderColor } },
    axisLabel: { color: mutedColor, fontSize: 11 },
    splitLine: { lineStyle: { color: borderColor, type: 'dashed' as const } },
    nameTextStyle: { color: mutedColor },
  }
}

export function buildTooltipTheme() {
  const { textColor, borderColor, cardBg } = getChartColors()
  return {
    backgroundColor: cardBg,
    borderColor,
    textStyle: { color: textColor, fontSize: 12 },
  }
}

export function buildLegendTheme() {
  const { mutedColor } = getChartColors()
  return {
    textStyle: { color: mutedColor, fontSize: 11 },
  }
}
