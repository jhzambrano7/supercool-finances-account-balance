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
