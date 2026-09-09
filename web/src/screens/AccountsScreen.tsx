import { useEffect, useState } from 'react'
import { ApiError, closeAccount, getAccounts } from '../api/client'
import type { AccountResponse } from '../api/types'
import type { DescribedError } from '../api/errors'
import { ErrorBanner } from '../components/ErrorBanner'
import { formatMinorUnits } from '../money/money'
import type { Currency } from '../money/money'
import { truncateId } from '../identity/identity'

interface Props {
  ownerId: string
}

/** Accounts, for real (`GET /accounts`, docs/web-ui-plan.md §6.1b) -- every account this caller
 * owns, with its real, current balance. No client-side registry, no replay hack. */
export function AccountsScreen({ ownerId }: Props) {
  const [accounts, setAccounts] = useState<AccountResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<DescribedError | null>(null)
  const [closingId, setClosingId] = useState<string | null>(null)
  const [closeError, setCloseError] = useState<DescribedError | null>(null)

  async function refresh() {
    setLoading(true)
    setError(null)
    try {
      const response = await getAccounts(ownerId)
      setAccounts(response.items)
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    // Re-fetch whenever the active identity changes -- each identity sees only its own accounts.
    // Also drop any close error left over from the previous identity's session, so switching
    // identities never shows a banner about an action nobody just took.
    setCloseError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownerId])

  /**
   * `POST /accounts/{id}/close` (docs/prd.md §7.2). Disabling the button for a non-zero balance
   * or an already-CLOSED account is a UX hint only -- the backend enforces the real rule
   * regardless, so a stale row (someone else deposited into it a second ago) still gets a real,
   * honest `422` here, never silently ignored.
   *
   * `refresh()` runs whether this succeeds or fails: a 422 here means the row was already stale
   * (a concurrent deposit landed first) -- refreshing shows the real current balance, so the same
   * click doesn't just repeat the same 422 against a UI that never caught up.
   */
  async function close(account: AccountResponse) {
    setClosingId(account.account_id)
    setCloseError(null)
    try {
      await closeAccount(account.account_id, ownerId)
    } catch (err) {
      if (err instanceof ApiError) setCloseError(err.described)
      else throw err
    } finally {
      await refresh()
      setClosingId(null)
    }
  }

  return (
    <div className="stack">
      <h2 className="screen-title">Accounts</h2>

      {error && <ErrorBanner error={error} onRetry={() => void refresh()} />}
      {closeError && <ErrorBanner error={closeError} onRetry={() => setCloseError(null)} />}

      {loading && !error && <div className="help-text">Loading…</div>}

      {!loading && !error && accounts.length === 0 && (
        <div className="help-text">No accounts yet. Open one to get started.</div>
      )}

      <div className="stack">
        {accounts.map((account) => {
          const closable = account.status === 'ACTIVE' && account.balance === 0
          return (
            <div key={account.account_id} className="card row" style={{ justifyContent: 'space-between' }}>
              <div>
                <div className="mono">{truncateId(account.account_id)}</div>
                <div className="help-text">
                  {account.purpose} · {account.currency} · {account.status}
                </div>
              </div>
              <div className="row">
                <div className="amount tabular">
                  {formatMinorUnits(account.balance, account.currency as Currency)}
                </div>
                {account.status === 'ACTIVE' && (
                  <button
                    className="btn-ghost"
                    disabled={!closable || closingId === account.account_id}
                    title={closable ? 'Close this account' : 'Only a zero-balance, active account can close (docs/prd.md §7.2)'}
                    onClick={() => void close(account)}
                  >
                    {closingId === account.account_id ? 'Closing…' : 'Close'}
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {!loading && (
        <button className="btn-ghost" onClick={() => void refresh()}>
          Refresh
        </button>
      )}
    </div>
  )
}
