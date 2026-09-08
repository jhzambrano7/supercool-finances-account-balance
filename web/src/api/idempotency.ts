/**
 * Idempotency keys, from the client side (docs/web-ui-plan.md §4).
 *
 * A key is minted per *intent*, not per *attempt*: once, before the first
 * request, and held alongside the pending body so a tab refresh mid-request
 * turns "did that go through?" into a question this module can answer.
 *
 * A pending operation is one of three kinds -- transfer, deposit, withdrawal --
 * each with its own body shape (`DepositRequest` has no `source_account_id`,
 * `WithdrawalRequest` has no `destination_account_id`). The `kind` tag is what
 * lets a resumed operation dispatch to the matching `retry*` client call.
 */

import type { DepositRequest, TransferRequest, WithdrawalRequest } from './types'

interface PendingBase {
  key: string
  callerId: string
  createdAt: string
}

export type PendingOperation =
  | (PendingBase & { kind: 'transfer'; body: TransferRequest })
  | (PendingBase & { kind: 'deposit'; body: DepositRequest })
  | (PendingBase & { kind: 'withdrawal'; body: WithdrawalRequest })

const PENDING_KEY = 'scf.pendingOperation'

export function mintIdempotencyKey(): string {
  return crypto.randomUUID()
}

export function savePendingOperation(pending: PendingOperation): void {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending))
  } catch {
    // best-effort; losing this only degrades the refresh-recovery banner
  }
}

export function loadPendingOperation(): PendingOperation | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY)
    return raw ? (JSON.parse(raw) as PendingOperation) : null
  } catch {
    return null
  }
}

export function clearPendingOperation(): void {
  try {
    localStorage.removeItem(PENDING_KEY)
  } catch {
    // best-effort
  }
}
