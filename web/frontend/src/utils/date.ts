const SHANGHAI_TZ = 'Asia/Shanghai'

export function getShanghaiDateString(date: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat(undefined, {
    timeZone: SHANGHAI_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date)

  const year = parts.find((part) => part.type === 'year')?.value
  const month = parts.find((part) => part.type === 'month')?.value
  const day = parts.find((part) => part.type === 'day')?.value

  if (!year || !month || !day) {
    throw new Error('Failed to format Shanghai date')
  }

  return `${year}-${month}-${day}`
}
