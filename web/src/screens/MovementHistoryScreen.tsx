import { useEffect, useState } from 'react'
import { ApiError, createReversal, getAccounts, getMovements } from '../api/client'
import type { AccountResponse, MovementResponse, TransferResponse } from '../api/types'
import type { DescribedError } from '../api/errors'
import { ADMIN_PRINCIPAL_ID } from '../identity/adminPrincipal'
import { ErrorBanner } from '../components/ErrorBanner'
import { Receipt } from '../components/Receipt'
import { mintIdempotencyKey } from '../api/idempotency'
import { formatSignedEntry } from '../money/money'
import type { Currency } from '../money/money'
import { truncateId } from '../identity/identity'

interface Props {
  ownerId: string
}

/** `GET /accounts/{account_id}/movements` (docs/web-ui-plan.md §6.2) -- a statement for one of the
 * caller's own accounts, newest first. Cursor pagination, not a numbered pager: the ledger has no
 * total count to page against, only a "load more" that asks for the next opaque page. */
export function MovementHistoryScreen({ ownerId }: Props) {
  const [accounts, setAccounts] = useState<AccountResponse[]>([])
  const [accountId, setAccountId] = useState('')
  const [movements, setMovements] = useState<MovementResponse[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<DescribedError | null>(null)
  const [reversing, setReversing] = useState<string | null>(null)
  const [reversal, setReversal] = useState<TransferResponse | null>(null)
  const [reversalError, setReversalError] = useState<DescribedError | null>(null)

  useEffect(() => {
    getAccounts(ownerId)
      .then((response) => setAccounts(response.items))
      .catch(() => {
        // Best-effort: an empty picker is a visible, honest failure mode.
      })
  }, [ownerId])

  async function loadFirstPage(id: string) {
    setAccountId(id)
    setMovements([])
    setCursor(null)
    setError(null)
    if (!id) return
    setLoading(true)
    try {
      const page = await getMovements(id, ownerId)
      setMovements(page.items)
      setCursor(page.next_cursor)
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setLoading(false)
    }
  }

  async function loadMore() {
    if (!cursor) return
    setLoading(true)
    setError(null)
    try {
      const page = await getMovements(accountId, ownerId, { cursor })
      setMovements((prev) => [...prev, ...page.items])
      setCursor(page.next_cursor)
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setLoading(false)
    }
  }

  /**
   * `X-Caller-Id` here is always the fixed admin principal (`README.md`, "Reversal is
   * operator-only"), never `ownerId` -- reversal has no per-account ownership rule at all (R1),
   * unlike every other action on this screen. No refresh-recovery persistence (unlike the
   * money-movement forms' `PendingOperation`): this is a single click, not a form with a confirm
   * step, and a request truly interrupted mid-flight just leaves the row clickable again -- a
   * retry that actually landed the first time gets an honest "already has a reversal" 409, never
   * a duplicate reversal.
   */
  async function reverse(movement: MovementResponse) {
    setReversing(movement.transfer_id)
    setReversal(null)
    setReversalError(null)
    try {
      const result = await createReversal(movement.transfer_id, {
        callerId: ADMIN_PRINCIPAL_ID,
        idempotencyKey: mintIdempotencyKey(),
      })
      setReversal(result)
      await loadFirstPage(accountId)
    } catch (err) {
      if (err instanceof ApiError) setReversalError(err.described)
      else throw err
    } finally {
      setReversing(null)
    }
  }

  const selected = accounts.find((a) => a.account_id === accountId)
  const currency = (selected?.currency ?? 'USD') as Currency

  return (
    <div className="stack">
      <h2 className="screen-title">Movement history</h2>

      <div className="card stack">
        <div className="field">
          <label>Account</label>
          <select value={accountId} onChange={(e) => void loadFirstPage(e.target.value)}>
            <option value="">Select an account…</option>
            {accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>
                {truncateId(a.account_id)} · {a.purpose} · {a.currency}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <ErrorBanner error={error} onRetry={() => void loadFirstPage(accountId)} />}

      {reversal && (
        <div className="stack">
          <Receipt transfer={reversal} heading="Reversal posted." />
          <button className="btn-ghost" onClick={() => setReversal(null)}>
            Dismiss
          </button>
        </div>
      )}

      {reversalError && (
        <ErrorBanner error={reversalError} onRetry={() => setReversalError(null)} />
      )}

      {accountId && !error && (
        <>
          {movements.length === 0 && !loading && (
            <div className="help-text">No movements on this account yet.</div>
          )}

          {movements.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Occurred at</th>
                  <th>Direction</th>
                  <th>Counterparty</th>
                  <th>Amount</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {movements.map((movement) => (
                  <tr key={movement.entry_id}>
                    <td>{new Date(movement.occurred_at).toLocaleString()}</td>
                    <td>{movement.direction}</td>
                    <td className="mono">{truncateId(movement.counterparty_account_id)}</td>
                    <td
                      className={`amount tabular ${movement.direction === 'DEBIT' ? 'amount-debit' : 'amount-credit'}`}
                    >
                      {formatSignedEntry(movement.amount, currency, movement.direction)}
                    </td>
                    <td>
                      <button
                        className="btn-ghost"
                        disabled={reversing === movement.transfer_id}
                        title="Reverses this row's transfer, authorized as the platform's one admin principal (README.md) -- not this screen's active identity."
                        onClick={() => void reverse(movement)}
                      >
                        {reversing === movement.transfer_id ? 'Reversing…' : 'Reverse (as admin)'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {cursor && (
            <button className="btn-ghost" disabled={loading} onClick={() => void loadMore()}>
              {loading ? 'Loading…' : 'Load more'}
            </button>
          )}
        </>
      )}
    </div>
  )
}
