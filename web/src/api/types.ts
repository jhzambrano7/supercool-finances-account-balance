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
