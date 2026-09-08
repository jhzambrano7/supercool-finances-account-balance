/**
 * One error mapper, one place (docs/web-ui-plan.md §7). Rules: branch on
 * `typeof detail` (FastAPI's own validation shape sends a list; every
 * `HTTPException` in this codebase sends a string); never render a raw
 * status code to the operator; never swallow the backend's `detail`
 * string, which is uniformly good.
 */

export type Recovery =
  | 'switch-identity'
  | 'change-leg'
  | 'start-over'
  | 'try-again'
  | 'edit-amount'
  | 'inline'
  | 'report'
  | 'focus-field'
  | 'retry'
  | 'none'

export interface DescribedError {
  status: number | null
  title: string
  detail: string | null
  recovery: Recovery
}

function detailToString(detail: unknown): string | null {
  if (detail == null) return null
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI's own validation shape: a list of {loc, msg, type}.
    return detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : JSON.stringify(item)))
      .join('; ')
  }
  return JSON.stringify(detail)
}

/** A missing `Idempotency-Key` header comes back as FastAPI's list shape; a blank one comes back as a string. */
function isMissingIdempotencyKey(status: number, rawDetail: unknown): boolean {
  return status === 422 && Array.isArray(rawDetail)
}

export function describeHttpError(status: number, rawDetail: unknown): DescribedError {
  const detail = detailToString(rawDetail)

  if (status === 401) {
    return {
      status,
      title: 'No identity is selected.',
      detail: detail ?? 'Every money movement needs one — there is no login here, just a simulated caller id.',
      recovery: 'switch-identity',
    }
  }

  if (status === 403) {
    const lower = (detail ?? '').toLowerCase()
    if (lower.includes('system')) {
      return {
        status,
        title: 'Both accounts are platform accounts.',
        detail,
        recovery: 'change-leg',
      }
    }
    return {
      status,
      title: "That account isn't yours.",
      detail: detail ?? 'Only the owner of the account being debited can move money out of it.',
      recovery: 'switch-identity',
    }
  }

  if (status === 404) {
    return {
      status,
      title: 'No account with that id.',
      detail,
      recovery: 'focus-field',
    }
  }

  if (status === 409) {
    const lower = (detail ?? '').toLowerCase()
    if (lower.includes('race') || lower.includes('already exists')) {
      return {
        status,
        title: 'Two requests raced to open this account.',
        detail: detail ?? 'Retrying will find whichever one won.',
        recovery: 'try-again',
      }
    }
    return {
      status,
      title: 'This confirmation was already used for a different transfer.',
      detail: detail ?? 'The retry code on this attempt belongs to a transfer with different details, so we stopped rather than post something you did not intend.',
      recovery: 'start-over',
    }
  }

  if (status === 422) {
    if (isMissingIdempotencyKey(status, rawDetail)) {
      return {
        status,
        title: "The app didn't send a retry code.",
        detail: detail ?? 'This is a bug in this page, not something you did.',
        recovery: 'report',
      }
    }
    const lower = (detail ?? '').toLowerCase()
    let title = 'The service rejected this input.'
    if (lower.includes('insufficient') || lower.includes('below zero')) {
      title = "Balances can't go negative from a customer action."
    } else if (lower.includes('currency')) {
      title = 'Both accounts must hold the same currency — this service does not convert.'
    }
    return { status, title, detail, recovery: 'edit-amount' }
  }

  if (status === 400) {
    return {
      status,
      title: 'The service rejected this on a rule the page did not anticipate.',
      detail,
      recovery: 'report',
    }
  }

  if (status >= 500) {
    return {
      status,
      title: 'The service is having trouble.',
      detail: detail ?? 'Your request may or may not have posted — retrying is safe, it carries the same retry code.',
      recovery: 'retry',
    }
  }

  return { status, title: 'Something unexpected happened.', detail, recovery: 'report' }
}

export function describeNetworkError(): DescribedError {
  return {
    status: null,
    title: "Couldn't reach the service.",
    detail: 'Check that the API is running and that the dev proxy is configured — a browser cannot read this API cross-origin (no CORS middleware).',
    recovery: 'retry',
  }
}
