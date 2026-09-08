import { useState } from 'react'
import { registerAccount } from '../accounts/registry'
import { ApiError, openAccount } from '../api/client'
import type { DescribedError } from '../api/errors'
import { ErrorBanner } from '../components/ErrorBanner'
import { truncateId } from '../identity/identity'

interface Props {
  ownerId: string
}

type Purpose = 'CHECKING' | 'SAVINGS'

const CURRENCIES: { code: string; enabled: boolean }[] = [
  { code: 'USD', enabled: true },
  { code: 'MXN', enabled: false },
  { code: 'COP', enabled: false },
]

/** `POST /accounts` (docs/web-ui-plan.md §5.2). 201 and 200 are both a normal, useful outcome. */
export function OpenAccountScreen({ ownerId }: Props) {
  const [ownerOverride, setOwnerOverride] = useState<string | null>(null)
  const [purpose, setPurpose] = useState<Purpose>('CHECKING')
  const [currency, setCurrency] = useState('USD')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<DescribedError | null>(null)
  const [result, setResult] = useState<{ status: 200 | 201; account: Awaited<ReturnType<typeof openAccount>>['account'] } | null>(null)

  const effectiveOwner = ownerOverride ?? ownerId

  async function submit() {
    setSubmitting(true)
    setError(null)
    try {
      const response = await openAccount({ owner_id: effectiveOwner, purpose, currency })
      setResult(response)
      registerAccount(effectiveOwner, {
        accountId: response.account.account_id,
        purpose,
        currency,
      })
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="stack">
      <h2 className="screen-title">Open account</h2>

      <div className="card stack">
        <div className="field">
          <label>Owner</label>
          {ownerOverride === null ? (
            <div className="row">
              <span className="mono">{truncateId(ownerId)}</span>
              <button className="btn-ghost" onClick={() => setOwnerOverride(ownerId)} style={{ padding: '4px 12px', fontSize: 12 }}>
                Open for a different owner
              </button>
            </div>
          ) : (
            <input value={ownerOverride} onChange={(e) => setOwnerOverride(e.target.value)} placeholder="owner uuid" />
          )}
        </div>

        <div className="field">
          <label>Purpose</label>
          <div className="row">
            {(['CHECKING', 'SAVINGS'] as Purpose[]).map((p) => (
              <label key={p} className="row" style={{ fontWeight: 400 }}>
                <input type="radio" name="purpose" checked={purpose === p} onChange={() => setPurpose(p)} style={{ height: 'auto' }} />
                {p}
              </label>
            ))}
          </div>
        </div>

        <div className="field">
          <label>Currency</label>
          <div className="row">
            {CURRENCIES.map((c) => (
              <label key={c.code} className="row" style={{ fontWeight: 400, opacity: c.enabled ? 1 : 0.5 }}>
                <input
                  type="radio"
                  name="currency"
                  disabled={!c.enabled}
                  checked={currency === c.code}
                  onChange={() => setCurrency(c.code)}
                  style={{ height: 'auto' }}
                />
                {c.code}
                {!c.enabled && <span className="help-text"> (awaiting per-currency funding accounts)</span>}
              </label>
            ))}
          </div>
        </div>

        <div>
          <button className="btn-primary" disabled={submitting} onClick={submit}>
            {submitting ? 'Opening…' : 'Open account'}
          </button>
        </div>
      </div>

      {error && (
        <ErrorBanner
          error={error}
          onRetry={error.recovery === 'try-again' ? submit : undefined}
        />
      )}

      {result && (
        <div className="card stack">
          <div>{result.status === 201 ? 'Account opened.' : 'You already have this account. Here it is.'}</div>
          <table>
            <tbody>
              <tr>
                <th>Id</th>
                <td className="mono">{result.account.account_id}</td>
              </tr>
              <tr>
                <th>Purpose</th>
                <td>{result.account.purpose}</td>
              </tr>
              <tr>
                <th>Currency</th>
                <td>{result.account.currency}</td>
              </tr>
              <tr>
                <th>Balance</th>
                <td className="amount">{result.account.balance}</td>
              </tr>
              <tr>
                <th>Status</th>
                <td>{result.account.status}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
