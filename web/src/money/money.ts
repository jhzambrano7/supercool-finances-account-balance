/**
 * Money: string arithmetic only. `parseFloat(x) * 100` never appears here.
 *
 * The backend's `Money` value object refuses to be constructed from a float,
 * for the same reason: `19.99 * 100` is `1998.9999999999998` in IEEE-754.
 * This module holds the same line on the way in, and the same discipline on
 * the way out (docs/web-ui-plan.md §3).
 */

export type Currency = 'USD' | 'MXN' | 'COP'

/**
 * Minor-unit exponents. The API does not tell us these; ISO 4217 does.
 *
 * Open question for the backend owner, not decided here: ISO 4217 assigns
 * COP an exponent of 2 (centavos), but Colombian pesos are quoted and
 * settled in whole units in practice. This follows ISO 4217 because the
 * domain's own docstring calls Currency "an ISO 4217 alphabetic code" --
 * change this one line (and re-check any COP fixture) if that's wrong.
 *
 * Only USD is enabled in the UI today (openspec/specs/account-balance/spec.md,
 * "Currency" -- narrowed to one member for now); MXN/COP stay in this table
 * as the worked example of what turning one on looks like.
 */
export const MINOR_UNIT_EXPONENT: Record<Currency, number> = {
  USD: 2,
  MXN: 2,
  COP: 2,
}

/** A `Money` amount already validated as a whole count of minor units. Never re-enters an input field. */
export type AmountMinor = number & { readonly __brand: 'AmountMinor' }

export type ParseResult =
  | { ok: true; value: AmountMinor }
  | { ok: false; error: string }

const MAX_SAFE_MINOR = Number.MAX_SAFE_INTEGER

/**
 * Human decimal string -> integer minor units. Rejects excess precision
 * rather than rounding it away -- `10.999 USD` is a typo, not `10.99` or
 * `11.00` in disguise, and silently picking one would be exactly the class
 * of quiet corruption this ledger exists to prevent.
 */
export function parseAmountToMinorUnits(raw: string, currency: Currency): ParseResult {
  const exponent = MINOR_UNIT_EXPONENT[currency]
  const trimmed = raw.trim().replace(/[\s,_](?=\d{3}(\D|$))/g, '').replace(/\s/g, '')

  if (trimmed === '') {
    return { ok: false, error: 'Enter an amount.' }
  }
  if (trimmed === '-' || trimmed === '+' || trimmed === '.' || trimmed === ',') {
    return { ok: false, error: 'That is not a complete amount.' }
  }
  if (/^[+-]/.test(trimmed) && trimmed[0] === '-') {
    return { ok: false, error: 'Amounts cannot be negative here.' }
  }

  const withoutSign = trimmed.replace(/^\+/, '')
  const separators = (withoutSign.match(/[.,]/g) ?? []).length
  if (separators > 1) {
    return { ok: false, error: 'Use a single decimal separator.' }
  }

  const normalized = withoutSign.replace(',', '.')
  const parts = normalized.split('.')
  const integerPart = parts[0] ?? ''
  const fractionPart = parts.length > 1 ? (parts[1] ?? '') : ''

  if (integerPart === '' && fractionPart === '') {
    return { ok: false, error: 'That is not a complete amount.' }
  }
  if (!/^\d*$/.test(integerPart) || !/^\d*$/.test(fractionPart)) {
    return { ok: false, error: 'Only digits and one decimal separator are allowed.' }
  }
  if (parts.length > 1 && fractionPart === '') {
    return { ok: false, error: 'There are no digits after the decimal separator.' }
  }
  if (fractionPart.length > exponent) {
    return {
      ok: false,
      error: `${currency} amounts have at most ${exponent} decimal place${exponent === 1 ? '' : 's'}.`,
    }
  }

  const paddedFraction = fractionPart.padEnd(exponent, '0')
  const digits = (integerPart === '' ? '0' : integerPart) + paddedFraction
  const asBigInt = BigInt(digits)

  if (asBigInt === 0n) {
    return { ok: false, error: 'Enter an amount greater than zero.' }
  }
  if (asBigInt > BigInt(MAX_SAFE_MINOR)) {
    return { ok: false, error: 'That amount is too large.' }
  }

  return { ok: true, value: Number(asBigInt) as AmountMinor }
}

/**
 * Integer minor units -> human decimal, as a string built by hand (sign,
 * pad, slice). `Intl.NumberFormat` is used only to resolve the locale's
 * grouping/decimal separators, never to do the division.
 */
export function formatMinorUnits(minor: number, currency: Currency): string {
  const exponent = MINOR_UNIT_EXPONENT[currency]
  const negative = minor < 0
  const digits = Math.abs(minor).toString().padStart(exponent + 1, '0')
  const splitAt = digits.length - exponent
  const integerDigits = exponent === 0 ? digits : digits.slice(0, splitAt)
  const fractionDigits = exponent === 0 ? '' : digits.slice(splitAt)

  const groupedInteger = Number(integerDigits).toLocaleString('en-US')
  const decimal = fractionDigits === '' ? '' : `.${fractionDigits}`

  return `${negative ? '−' : ''}${groupedInteger}${decimal} ${currency}`
}

/** Signed display for a ledger entry: `direction` carries the sign, never the raw number (§3.4). */
export function formatSignedEntry(minor: number, currency: Currency, direction: 'DEBIT' | 'CREDIT'): string {
  const magnitude = formatMinorUnits(Math.abs(minor), currency)
  return direction === 'DEBIT' ? `−${magnitude}` : `+${magnitude}`
}
