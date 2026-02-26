const frenchDateFormatter = new Intl.DateTimeFormat('fr-CH', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})

export function formatFrenchDate(isoDate: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) {
    return isoDate
  }

  const date = new Date(`${isoDate}T00:00:00.000Z`)
  if (Number.isNaN(date.getTime())) {
    return isoDate
  }

  return frenchDateFormatter.format(date)
}

export function normalizeQuickReplyDisplay(label?: string, value?: string): string {
  const candidate = (label ?? value ?? '').trim()
  if (!candidate) {
    return ''
  }

  if (candidate === '✅') {
    return 'Oui.'
  }

  if (candidate === '❌') {
    return 'Non.'
  }

  const capitalized = candidate.charAt(0).toLocaleUpperCase('fr-CH') + candidate.slice(1)
  if (/[.!?…]$/.test(capitalized)) {
    return capitalized
  }

  return `${capitalized}.`
}
