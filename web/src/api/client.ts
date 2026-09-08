import { describeHttpError, describeNetworkError, type DescribedError } from './errors'
import type {
  AccountResponse,
  AccountsResponse,
  DepositRequest,
  MovementsResponse,
  OpenAccountRequest,
  TransferRequest,
  TransferResponse,
  WithdrawalRequest,
} from './types'

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

interface MoneyMovementHeaders {
  callerId: string
  idempotencyKey: string
}

/**
 * One POST, one path, one body -- shared by `/transfers`, `/deposits` and `/withdrawals`: all
 * three take the same two headers and return the same `TransferResponse` shape (a deposit or
 * withdrawal *is* a `Transfer`, spec Purpose), so there is nothing operation-specific left once
 * the path and body are parameters.
 */
async function postMoneyMovementOnce<TBody>(
  path: string,
  body: TBody,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  let response: Response
  try {
    response = await fetch(path, {
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
 * The client's own retry policy (§4.3): retries on network failure and on `5xx` only, twice, at
 * 1s then 3s -- never on a `4xx`, which is a decided answer, not a transient one (this includes
 * `503 CurrencyNotOperationalError`: a currency with no seeded platform account will not become
 * seeded by retrying). Every retry reuses the same idempotency key, which is the entire point of
 * minting it once.
 */
async function postMoneyMovementWithRetry<TBody>(
  path: string,
  body: TBody,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  let lastError: ApiError | null = null

  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    try {
      return await postMoneyMovementOnce(path, body, headers)
    } catch (err) {
      const apiError = err as ApiError
      lastError = apiError
      // 503 excluded despite being >= 500: CurrencyNotOperationalError is a deterministic
      // configuration gap, not a transient fault -- retrying it wastes the 1s/3s delay for
      // an outcome that will not change.
      const status = apiError.described.status
      const isRetryable = status === null || (status >= 500 && status !== 503)
      if (!isRetryable || attempt === RETRY_DELAYS_MS.length) {
        throw apiError
      }
      await sleep(RETRY_DELAYS_MS[attempt])
    }
  }

  // Unreachable: the loop above always returns or throws.
  throw lastError ?? new ApiError(describeNetworkError())
}

export async function createTransfer(
  body: TransferRequest,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  return postMoneyMovementWithRetry('/transfers', body, headers)
}

/** A single manual retry, invoked from a "Retry" button -- reuses the same key, no new attempt-count logic. */
export async function retryTransfer(
  body: TransferRequest,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  return postMoneyMovementOnce('/transfers', body, headers)
}

export async function createDeposit(
  body: DepositRequest,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  return postMoneyMovementWithRetry('/deposits', body, headers)
}

export async function retryDeposit(
  body: DepositRequest,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  return postMoneyMovementOnce('/deposits', body, headers)
}

export async function createWithdrawal(
  body: WithdrawalRequest,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  return postMoneyMovementWithRetry('/withdrawals', body, headers)
}

export async function retryWithdrawal(
  body: WithdrawalRequest,
  headers: MoneyMovementHeaders,
): Promise<TransferResponse> {
  return postMoneyMovementOnce('/withdrawals', body, headers)
}

/**
 * One GET, shared by the three read endpoints below -- no retry policy here, unlike the money
 * movements above: a `GET` is not a decided-once action guarded by an idempotency key, so a
 * caller that wants a retry just issues the same request again.
 */
async function getJson(path: string, callerId: string): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, { headers: { 'X-Caller-Id': callerId } })
  } catch {
    throw new ApiError(describeNetworkError())
  }

  const parsed = await parseJsonBody(response)
  if (!response.ok) {
    throw new ApiError(describeHttpError(response.status, (parsed as { detail?: unknown } | null)?.detail))
  }
  return parsed
}

/** `GET /accounts` -- every account the caller owns. */
export async function getAccounts(callerId: string): Promise<AccountsResponse> {
  return (await getJson('/accounts', callerId)) as AccountsResponse
}

/** `GET /accounts/{account_id}` -- 403 if the caller doesn't own it, 404 if it doesn't exist. */
export async function getAccount(accountId: string, callerId: string): Promise<AccountResponse> {
  return (await getJson(`/accounts/${accountId}`, callerId)) as AccountResponse
}

/**
 * `GET /accounts/{account_id}/movements` -- cursor pagination, newest first. `cursor` is opaque;
 * omit it for the first page. `limit` defaults to 25 server-side, caps at 100.
 */
export async function getMovements(
  accountId: string,
  callerId: string,
  options: { limit?: number; cursor?: string | null } = {},
): Promise<MovementsResponse> {
  const params = new URLSearchParams()
  if (options.limit != null) params.set('limit', String(options.limit))
  if (options.cursor != null) params.set('cursor', options.cursor)
  const query = params.toString()
  const path = `/accounts/${accountId}/movements${query ? `?${query}` : ''}`
  return (await getJson(path, callerId)) as MovementsResponse
}
