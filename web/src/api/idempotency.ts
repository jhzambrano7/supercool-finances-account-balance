/**
 * Idempotency keys, from the client side (docs/web-ui-plan.md §4).
 *
 * A key is minted per *intent*, not per *attempt*: once, before the first
 * request, and held alongside the pending body so a tab refresh mid-request
 * turns "did that go through?" into a question this module can answer.
 */

import type { TransferRequest } from './types'

export interface PendingTransfer {
  key: string
  body: TransferRequest
  callerId: string
  createdAt: string
}

const PENDING_KEY = 'scf.pendingTransfer'

export function mintIdempotencyKey(): string {
  return crypto.randomUUID()
}

export function savePendingTransfer(pending: PendingTransfer): void {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending))
  } catch {
    // best-effort; losing this only degrades the refresh-recovery banner
  }
}

export function loadPendingTransfer(): PendingTransfer | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY)
    return raw ? (JSON.parse(raw) as PendingTransfer) : null
  } catch {
    return null
  }
}

export function clearPendingTransfer(): void {
  try {
    localStorage.removeItem(PENDING_KEY)
  } catch {
    // best-effort
  }
}
