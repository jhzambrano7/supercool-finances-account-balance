import { describeHttpError, describeNetworkError, type DescribedError } from './errors'
import type { AccountResponse, OpenAccountRequest, TransferRequest, TransferResponse } from './types'

export class ApiError extends Error {
  described: DescribedError
  constructor(described: DescribedError) {
    super(described.title)
    this.described = described
  }
}

async function parseJsonBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

/** `POST /accounts` -- returns which status arrived so the caller can distinguish "opened" from "already existed" (AO3). */
export async function openAccount(
  body: OpenAccountRequest,
): Promise<{ status: 200 | 201; account: AccountResponse }> {
  let response: Response
  try {
    response = await fetch('/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(describeNetworkError())
  }

  const parsed = await parseJsonBody(response)
  if (!response.ok) {
    throw new ApiError(describeHttpError(response.status, (parsed as { detail?: unknown } | null)?.detail))
  }
  return { status: response.status as 200 | 201, account: parsed as AccountResponse }
}

interface TransferHeaders {
  callerId: string
  idempotencyKey: string
}

async function postTransferOnce(
  body: TransferRequest,
  headers: TransferHeaders,
): Promise<TransferResponse> {
  let response: Response
  try {
    response = await fetch('/transfers', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Caller-Id': headers.callerId,
        'Idempotency-Key': headers.idempotencyKey,
      },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(describeNetworkError())
  }

  const parsed = await parseJsonBody(response)
  if (!response.ok) {
    throw new ApiError(describeHttpError(response.status, (parsed as { detail?: unknown } | null)?.detail))
  }
  return parsed as TransferResponse
}

const RETRY_DELAYS_MS = [1000, 3000]

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * `POST /transfers` with the client's own retry policy (§4.3): retries on
 * network failure and on `5xx` only, twice, at 1s then 3s -- never on a
 * `4xx`, which is a decided answer, not a transient one. Every retry reuses
 * the same idempotency key, which is the entire point of minting it once.
 */
export async function createTransfer(
  body: TransferRequest,
  headers: TransferHeaders,
): Promise<TransferResponse> {
  let lastError: ApiError | null = null

  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    try {
      return await postTransferOnce(body, headers)
    } catch (err) {
      const apiError = err as ApiError
      lastError = apiError
      const isRetryable = apiError.described.status === null || apiError.described.status >= 500
      if (!isRetryable || attempt === RETRY_DELAYS_MS.length) {
        throw apiError
      }
      await sleep(RETRY_DELAYS_MS[attempt])
    }
  }

  // Unreachable: the loop above always returns or throws.
  throw lastError ?? new ApiError(describeNetworkError())
}

/** A single manual retry, invoked from a "Retry" button -- reuses the same key, no new attempt-count logic. */
export async function retryTransfer(
  body: TransferRequest,
  headers: TransferHeaders,
): Promise<TransferResponse> {
  return postTransferOnce(body, headers)
}
