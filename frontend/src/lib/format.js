export function plainReason(value) {
  return String(value || '')
    .replace(/(\d(?:\.\d)?)\u2605/g, '$1 out of 5')
    .replace(/\u2605/g, '')
    .trim()
}

export function shortReason(value) {
  const text = plainReason(value)
  const predicted = text.match(/predicted to give this (\d(?:\.\d)?) out of 5/i)
  if (predicted) return `Predicted ${predicted[1]} / 5 from similar raters`
  return text.split(' \u2014 ')[0]
}

export function formatRating(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(1) : '\u2014'
}

export function formatScore(value) {
  const number = Number(value)
  return Number.isFinite(number) ? `${Math.round(number)}%` : null
}

export function formatRuntime(minutes) {
  const total = Number(minutes)
  if (!Number.isFinite(total) || total <= 0) return null
  const hours = Math.floor(total / 60)
  const rest = total % 60
  if (!hours) return `${rest} min`
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

export function formatDate(value, { short = false } = {}) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: short ? 'short' : 'long',
    year: 'numeric',
  }).format(date)
}

export function formatMoney(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return null
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(number)
}

export function formatCount(value) {
  return new Intl.NumberFormat('en-US').format(Number(value) || 0)
}

export function initials(name) {
  const words = String(name || '')
    .replace(/^@/, '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (!words.length) return 'C'
  return words
    .slice(0, 2)
    .map((word) => word[0])
    .join('')
    .toUpperCase()
}

export function sentimentTone(compound) {
  if (compound >= 0.35) return 'positive'
  if (compound <= -0.35) return 'negative'
  return 'mixed'
}

export function listPhrase(items = [], limit = 3) {
  const list = items.filter(Boolean)
  if (!list.length) return null
  if (list.length <= limit) {
    if (list.length === 1) return list[0]
    return `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}`
  }
  return `${list.slice(0, limit).join(', ')} and more`
}
