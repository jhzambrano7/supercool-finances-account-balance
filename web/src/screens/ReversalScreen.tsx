import { useState } from 'react'
import { ApiError, createReversal } from '../api/client'
import type { DescribedError } from '../api/errors'
import type { TransferResponse } from '../api/types'
import { ErrorBanner } from '../components/ErrorBanner'
import { Receipt } from '../components/Receipt'
import { mintIdempotencyKey } from '../api/idempotency'
import { ADMIN_PRINCIPAL_ID } from '../identity/adminPrincipal'
import { truncateId } from '../identity/identity'

interface Props {
  ownerId: string
}

/**
 * `POST /transfers/{transfer_id}/reversals` (R1) -- operator-only, no per-account ownership rule
 * at all. Unlike every other screen here, this one does not send the active identity's id as a
 * convenience default: `X-Caller-Id` is always whichever identity is active in the bar above, and
 * a caller who is not the platform's one admin principal genuinely gets refused (403), same as the
 * real backend would refuse them. Switching to the admin identity first (via the bar's "Paste
 * identity", pasting the id shown below) is a real step, not a formality this screen papers over.
 */
export function ReversalScreen({ ownerId }: Props) {
  const [transferId, setTransferId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [receipt, setReceipt] = useState<TransferResponse | null>(null)
  const [error, setError] = useState<DescribedError | null>(null)

  const isAdmin = ownerId === ADMIN_PRINCIPAL_ID

  async function reverse() {
    const trimmed = transferId.trim()
    if (!trimmed) return
    setSubmitting(true)
    setReceipt(null)
    setError(null)
    try {
      const result = await createReversal(trimmed, {
        callerId: ownerId,
        idempotencyKey: mintIdempotencyKey(),
      })
      setReceipt(result)
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="stack">
      <h2 className="screen-title">Reversal</h2>

      <div className="card stack">
        <div>
          Reversal is operator-only (<code>README.md</code>) -- the platform&apos;s one admin
          principal is:
        </div>
        <div className="mono">{ADMIN_PRINCIPAL_ID}</div>
        <div className="help-text">
          Switch to it via the identity bar&apos;s &quot;Paste identity&quot; before reversing, or this
          will correctly come back as a 403 -- this screen never sends anything but the currently
          active identity.
        </div>
        <div className="help-text">
          Active identity: <span className="mono">{truncateId(ownerId)}</span>
          {isAdmin ? ' -- this is the admin principal.' : ' -- not the admin principal yet.'}
        </div>
      </div>

      <div className="card stack">
        <div className="field">
          <label>Transfer id to reverse</label>
          <input value={transferId} onChange={(e) => setTransferId(e.target.value)} placeholder="transfer uuid" />
          <div className="help-text">Find one on the Movement history screen&apos;s "Transfer id" column.</div>
        </div>
        <div>
          <button className="btn-primary" disabled={submitting || !transferId.trim()} onClick={() => void reverse()}>
            {submitting ? 'Reversing…' : 'Reverse'}
          </button>
        </div>
      </div>

      {error && <ErrorBanner error={error} onSwitchIdentity={() => setError(null)} />}

      {receipt && <Receipt transfer={receipt} heading="Reversal posted." />}
    </div>
  )
}
