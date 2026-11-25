const maximum = BigInt(Number.MAX_SAFE_INTEGER)

/** Parse a decimal RUB input without ever multiplying binary floating point. */
export function parseRubles(value: string): number {
  const normalized = value.trim().replace(',', '.')
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) throw new Error('Enter a positive amount with at most two decimal places.')
  const [whole, fraction = ''] = normalized.split('.')
  const minor = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
  if (minor <= 0n) throw new Error('Amount must be greater than zero.')
  if (minor > maximum) throw new Error('Amount exceeds the supported limit.')
  return Number(minor)
}

export function money(value: number | null | undefined, currency = 'RUB'): string {
  if (value === undefined || value === null) return '—'
  if (!Number.isSafeInteger(value)) return 'Invalid amount'
  const integer = BigInt(value)
  const absolute = integer < 0n ? -integer : integer
  const rubles = new Intl.NumberFormat('en-US').format(absolute / 100n)
  return `${integer < 0n ? '−' : ''}${rubles}.${String(absolute % 100n).padStart(2, '0')} ${currency === 'RUB' ? '₽' : currency}`
}

export function shortId(value: string | null | undefined): string { return value ? `${value.slice(0, 8)}…` : '—' }
export function dateTime(value: string | null | undefined): string { return value ? new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—' }
export function label(value: string): string { return value.replaceAll('_', ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase()) }
