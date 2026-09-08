import { useEffect, useState } from 'react'
import { ApiError, getAccounts, getMovements } from '../api/client'
import type { AccountResponse, MovementResponse } from '../api/types'
import type { DescribedError } from '../api/errors'
import { ErrorBanner } from '../components/ErrorBanner'
import { formatSignedEntry } from '../money/money'
import type { Currency } from '../money/money'
import { truncateId } from '../identity/identity'

interface Props {
  ownerId: string
}

/** `GET /accounts/{account_id}/movements` (docs/web-ui-plan.md §6.2) -- a statement for one of the
 * caller's own accounts, newest first. Cursor pagination, not a numbered pager: the ledger has no
 * total count to page against, only a "load more" that asks for the next opaque page.
 *
 * A pure read screen -- reversal lives on its own screen (`ReversalScreen.tsx`), authorized as
 * whichever identity is active there, never as an override this screen injects on the caller's
 * behalf. */
export function MovementHistoryScreen({ ownerId }: Props) {
  const [accounts, setAccounts] = useState<AccountResponse[]>([])
  const [accountId, setAccountId] = useState('')
  const [movements, setMovements] = useState<MovementResponse[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<DescribedError | null>(null)

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
                  <th>Transfer id</th>
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
                    <td className="mono">{truncateId(movement.transfer_id)}</td>
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
