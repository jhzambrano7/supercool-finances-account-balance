import { useEffect, useState } from 'react'
import { ApiError, getAccounts } from '../api/client'
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownerId])

  return (
    <div className="stack">
      <h2 className="screen-title">Accounts</h2>

      {error && <ErrorBanner error={error} onRetry={() => void refresh()} />}

      {loading && !error && <div className="help-text">Loading…</div>}

      {!loading && !error && accounts.length === 0 && (
        <div className="help-text">No accounts yet. Open one to get started.</div>
      )}

      <div className="stack">
        {accounts.map((account) => (
          <div key={account.account_id} className="card row" style={{ justifyContent: 'space-between' }}>
            <div>
              <div className="mono">{truncateId(account.account_id)}</div>
              <div className="help-text">
                {account.purpose} · {account.currency} · {account.status}
              </div>
            </div>
            <div className="amount tabular">
              {formatMinorUnits(account.balance, account.currency as Currency)}
            </div>
          </div>
        ))}
      </div>

      {!loading && (
        <button className="btn-ghost" onClick={() => void refresh()}>
          Refresh
        </button>
      )}
    </div>
  )
}
