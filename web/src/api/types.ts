/**
 * Mirrors of the backend's own DTOs
 * (src/modules/account_balance/adapters/inbound/api/dtos.py). Kept as plain
 * types, not re-derived or generated -- four shapes, not worth a codegen
 * step for this surface.
 */

export type AccountPurpose = 'CHECKING' | 'SAVINGS' | 'FUNDING' | 'SETTLEMENT'
export type AccountStatus = 'ACTIVE' | 'CLOSED'
export type EntryDirection = 'DEBIT' | 'CREDIT'

export interface OpenAccountRequest {
  owner_id: string
  purpose: 'CHECKING' | 'SAVINGS'
  currency: string
}

export interface AccountResponse {
  account_id: string
  owner_id: string
  purpose: AccountPurpose
  currency: string
  balance: number
  status: AccountStatus
}

export interface TransferRequest {
  source_account_id: string
  destination_account_id: string
  amount: number
  currency: string
}

/** `POST /deposits`'s body -- no `source_account_id`: the platform's `FUNDING` account for
 * `currency` is resolved server-side, never supplied by the caller. */
export interface DepositRequest {
  destination_account_id: string
  amount: number
  currency: string
}

/** `POST /withdrawals`'s body -- the deposit request's mirror image. */
export interface WithdrawalRequest {
  source_account_id: string
  amount: number
  currency: string
}

export interface EntryResponse {
  entry_id: string
  account_id: string
  direction: EntryDirection
  amount: number
}

export interface TransferResponse {
  transfer_id: string
  source_account_id: string
  destination_account_id: string
  amount: number
  currency: string
  requested_by: string
  occurred_at: string
  entries: EntryResponse[]
}

/** `GET /accounts`'s body -- wrapped in `{items: [...]}` rather than a bare array, so a
 * `next_cursor` could be added later without a breaking change (this endpoint has no
 * pagination today). */
export interface AccountsResponse {
  items: AccountResponse[]
}

/** `GET /accounts/{account_id}/movements`'s per-item shape -- narrower than `EntryResponse`
 * (no bare `account_id`, the caller already named it in the URL) and wider in another way
 * (`currency`, `counterparty_account_id`, `requested_by`, `occurred_at`). Deliberately no
 * running-balance field -- computing one would resurrect a cost this design avoids. */
export interface MovementResponse {
  entry_id: string
  transfer_id: string
  direction: EntryDirection
  amount: number
  currency: string
  counterparty_account_id: string
  requested_by: string
  occurred_at: string
}

/** `GET /accounts/{account_id}/movements`'s body -- cursor pagination, not offset: `next_cursor`
 * is `null` exactly when this page reached the end. */
export interface MovementsResponse {
  items: MovementResponse[]
  next_cursor: string | null
}

/** `GET /collections`'s per-account row (PRD §11.3) -- carries `owner_id`, unlike
 * `AccountResponse`: this endpoint is operator-only and exists precisely to say who owes what.
 *
 * `negative_since`/`age_seconds` are `null` together exactly when the account is negative but no
 * entry history explains it (PRD §11.1's balance drift) -- never a guessed timestamp or a `0`. */
export interface NegativeAccountResponse {
  account_id: string
  owner_id: string
  balance: number
  currency: string
  negative_since: string | null
  age_seconds: number | null
}

export interface CurrencyExposureResponse {
  currency: string
  count: number
  total_owed: number
}

export type NegativeAgeBucket =
  | 'UNDER_ONE_DAY'
  | 'ONE_TO_SEVEN_DAYS'
  | 'SEVEN_TO_THIRTY_DAYS'
  | 'OVER_THIRTY_DAYS'

export interface AgeBucketResponse {
  bucket: NegativeAgeBucket
  count: number
}

/** `GET /collections`'s body -- the stats an operator needs at a glance, plus the rows that back
 * them up. `age_buckets` always arrives in ascending order (the backend's own
 * `NEGATIVE_AGE_BUCKET_ORDER`), never re-sorted here. `unexplained_count` is a materialized-balance
 * drift (PRD §11.1), never folded into `count` silently -- an account counted there also appears
 * in `accounts` with `negative_since: null`. */
export interface CollectionsReportResponse {
  as_of: string
  count: number
  unexplained_count: number
  oldest_negative_since: string | null
  exposures: CurrencyExposureResponse[]
  age_buckets: AgeBucketResponse[]
  accounts: NegativeAccountResponse[]
}
