import { useState } from 'react'
import { forgetAccount, listRegisteredAccounts, registerAccount, type RegisteredAccount } from '../accounts/registry'
import { ApiError, openAccount } from '../api/client'
import { formatMinorUnits } from '../money/money'
import type { Currency } from '../money/money'

interface Props {
  ownerId: string
}

interface Balance {
  value: number | null
  error: string | null
  loading: boolean
}

/**
 * Accounts, as a client-side registry (docs/web-ui-plan.md §5.3). This list lives in the browser,
 * not on the server -- there is no `GET /accounts` yet.
 */
export function AccountsScreen({ ownerId }: Props) {
  const [accounts, setAccounts] = useState<RegisteredAccount[]>(() => listRegisteredAccounts(ownerId))
  const [balances, setBalances] = useState<Record<string, Balance>>({})
  const [addId, setAddId] = useState('')

  function refresh() {
    setAccounts(listRegisteredAccounts(ownerId))
  }

  function addById() {
    const trimmed = addId.trim()
    if (!trimmed) return
    registerAccount(ownerId, { accountId: trimmed, purpose: null, currency: null })
    setAddId('')
    refresh()
  }

  function forget(accountId: string) {
    forgetAccount(ownerId, accountId)
    refresh()
  }

  async function refreshBalance(account: RegisteredAccount) {
    if (!account.purpose || !account.currency) {
      setBalances((prev) => ({
        ...prev,
        [account.accountId]: { value: null, error: 'Added by id — no natural key to replay.', loading: false },
      }))
      return
    }
    setBalances((prev) => ({ ...prev, [account.accountId]: { value: null, error: null, loading: true } }))
    try {
      const response = await openAccount({ owner_id: ownerId, purpose: account.purpose, currency: account.currency })
      setBalances((prev) => ({
        ...prev,
        [account.accountId]: { value: response.account.balance, error: null, loading: false },
      }))
    } catch (err) {
      const message = err instanceof ApiError ? err.described.title : 'Could not refresh.'
      setBalances((prev) => ({ ...prev, [account.accountId]: { value: null, error: message, loading: false } }))
    }
  }

  return (
    <div className="stack">
      <h2 className="screen-title">Accounts</h2>

      <div className="banner banner--info">
        This list lives in your browser, not on the server. The API has no endpoint to list accounts
        yet. Accounts opened elsewhere won&apos;t appear here, and if the database is reset these will
        point at rows that no longer exist.
      </div>

      <div className="card row">
        <input value={addId} onChange={(e) => setAddId(e.target.value)} placeholder="account uuid" style={{ flex: 1 }} />
        <button className="btn-ghost" onClick={addById}>
          Add by id
        </button>
      </div>

      {accounts.length === 0 && <div className="help-text">No accounts yet. Open one to get started.</div>}

      <div className="stack">
        {accounts.map((account) => {
          const balance = balances[account.accountId]
          return (
            <div key={account.accountId} className="card row" style={{ justifyContent: 'space-between' }}>
              <div>
                <div className="mono">{account.accountId}</div>
                <div className="help-text">
                  {account.purpose ?? 'unknown purpose'} · {account.currency ?? 'unknown currency'}
                </div>
                {balance?.loading && <div className="help-text">Refreshing…</div>}
                {balance?.error && <div className="help-text">{balance.error}</div>}
                {balance?.value != null && account.currency && (
                  <div className="amount tabular">{formatMinorUnits(balance.value, account.currency as Currency)}</div>
                )}
              </div>
              <div className="row">
                <button
                  className="btn-ghost"
                  title="Refresh (via account-open replay)"
                  onClick={() => refreshBalance(account)}
                >
                  Refresh (via account-open replay)
                </button>
                <button className="btn-ghost" onClick={() => forget(account.accountId)}>
                  Forget
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
