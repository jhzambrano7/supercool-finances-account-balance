import { useEffect, useState } from 'react'
import { ApiError, getCollectionsReport } from '../api/client'
import type { AgeBucketResponse, CollectionsReportResponse } from '../api/types'
import type { DescribedError } from '../api/errors'
import { ErrorBanner } from '../components/ErrorBanner'
import { formatMinorUnits } from '../money/money'
import type { Currency } from '../money/money'
import { ADMIN_PRINCIPAL_ID } from '../identity/adminPrincipal'
import { truncateId } from '../identity/identity'

interface Props {
  ownerId: string
}

const BUCKET_LABELS: Record<AgeBucketResponse['bucket'], string> = {
  UNDER_ONE_DAY: 'Under 1 day',
  ONE_TO_SEVEN_DAYS: '1–7 days',
  SEVEN_TO_THIRTY_DAYS: '7–30 days',
  OVER_THIRTY_DAYS: 'Over 30 days',
}

/** Humanized duration for `age_seconds` -- the coarsest unit that still tells the operator
 * something ("3d", not "3d 4h 12m 9s"), matching the age buckets' own coarseness. `null` means the
 * account has no explaining entry history (PRD §11.1's balance drift) -- there is no age to show,
 * so this says so rather than rendering a `0` or NaN that would read as "just went negative". */
function formatAge(seconds: number | null): string {
  if (seconds === null) return 'unknown'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  return `${days}d`
}

/**
 * `GET /collections` (PRD §11.3) -- the credit exposure created by reversals (§7.3), and how long
 * each negative balance has been open. Operator-only, exactly like `ReversalScreen`: `X-Caller-Id`
 * is always whichever identity is active in the bar above, never overridden by this screen, so a
 * caller who has not switched to the admin principal correctly sees the real `403` the backend
 * would give any other caller.
 */
export function CollectionsScreen({ ownerId }: Props) {
  const [report, setReport] = useState<CollectionsReportResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<DescribedError | null>(null)

  const isAdmin = ownerId === ADMIN_PRINCIPAL_ID

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const result = await getCollectionsReport(ownerId)
      setReport(result)
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // Re-fetch whenever the active identity changes -- switching to/away from the admin
    // principal should immediately show whether this call is now authorized.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownerId])

  // Defaulted, not assumed present: a shape surprise (a malformed or unexpected response slipping
  // past `getJson`'s own content-type guard) should degrade this screen to "nothing to show", not
  // throw during render and blank the whole app -- there is no error boundary above this screen.
  const exposures = report?.exposures ?? []
  const ageBuckets = report?.age_buckets ?? []
  const accounts = report?.accounts ?? []
  const maxBucketCount = Math.max(1, ...ageBuckets.map((b) => b.count))

  return (
    <div className="stack">
      <h2 className="screen-title">Collections</h2>

      <div className="card stack">
        <div>
          Collections is operator-only (<code>README.md</code>) -- the platform&apos;s one admin
          principal is:
        </div>
        <div className="mono">{ADMIN_PRINCIPAL_ID}</div>
        <div className="help-text">
          Active identity: <span className="mono">{truncateId(ownerId)}</span>
          {isAdmin ? ' -- this is the admin principal.' : ' -- not the admin principal yet, switch via the identity bar above.'}
        </div>
      </div>

      {error && <ErrorBanner error={error} onRetry={() => void load()} />}

      {loading && !error && <div className="help-text">Loading…</div>}

      {!loading && !error && report && (
        <>
          {report.unexplained_count > 0 && (
            <div className="banner banner--error" role="alert">
              <strong>{report.unexplained_count}</strong> account
              {report.unexplained_count === 1 ? ' is' : 's are'} negative with no entry history
              that explains it (rows below marked &quot;Unexplained&quot;) -- this is a
              materialized-balance drift (docs/prd.md §11.1), not an ordinary collections case. It
              still counts toward the totals above; investigate before treating it as routine.
            </div>
          )}

          <div className="card stack">
            <div className="section-heading">Exposure</div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--space-6)' }}>
              <div>
                <div className="help-text">Negative accounts</div>
                <div className="amount-display">{report.count}</div>
              </div>
              {exposures.length === 0 && report.count === 0 && (
                <div className="help-text">No negative balances right now.</div>
              )}
              {exposures.map((exposure) => (
                <div key={exposure.currency}>
                  <div className="help-text">
                    Total owed ({exposure.currency}) · {exposure.count} account
                    {exposure.count === 1 ? '' : 's'}
                  </div>
                  <div className="amount-display amount-debit">
                    {formatMinorUnits(exposure.total_owed, exposure.currency as Currency)}
                  </div>
                </div>
              ))}
              <div>
                <div className="help-text">Oldest negative since</div>
                <div className="amount-display">
                  {report.oldest_negative_since
                    ? new Date(report.oldest_negative_since).toLocaleString()
                    : '—'}
                </div>
              </div>
            </div>
          </div>

          <div className="card stack">
            <div className="section-heading">Age distribution</div>
            <div className="help-text">
              Descriptive only, not a write-off policy (docs/prd.md §12 leaves that decision open)
              -- a day-old balance is a collections case, a month-old one is a write-off nobody
              decided on (docs/prd.md §11.3). Unexplained accounts (above, if any) have no known
              age and are not counted in any bar here.
            </div>
            <div className="stack" style={{ gap: 'var(--space-2)' }}>
              {ageBuckets.map((bucket) => (
                <div key={bucket.bucket} className="row">
                  <div style={{ width: 120 }} className="help-text">
                    {BUCKET_LABELS[bucket.bucket]}
                  </div>
                  <div style={{ flex: 1, background: 'var(--page)', borderRadius: 4, overflow: 'hidden' }}>
                    <div
                      style={{
                        width: `${(bucket.count / maxBucketCount) * 100}%`,
                        background: bucket.bucket === 'OVER_THIRTY_DAYS' ? 'var(--negative)' : 'var(--warning)',
                        height: 10,
                        borderRadius: 4,
                      }}
                    />
                  </div>
                  <div className="mono tabular" style={{ width: 24, textAlign: 'right' }}>
                    {bucket.count}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {accounts.length === 0 ? (
            <div className="help-text">No accounts are currently negative.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Owner</th>
                  <th>Balance</th>
                  <th>Negative since</th>
                  <th>Age</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.account_id}>
                    <td className="mono">{truncateId(account.account_id)}</td>
                    <td className="mono">{truncateId(account.owner_id)}</td>
                    <td className="amount tabular amount-debit">
                      {formatMinorUnits(account.balance, account.currency as Currency)}
                    </td>
                    <td>
                      {account.negative_since
                        ? new Date(account.negative_since).toLocaleString()
                        : 'Unexplained'}
                    </td>
                    <td className="tabular">{formatAge(account.age_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <button className="btn-ghost" onClick={() => void load()}>
            Refresh
          </button>
        </>
      )}
    </div>
  )
}
