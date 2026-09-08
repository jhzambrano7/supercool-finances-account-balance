import { useState } from 'react'
import { listRegisteredAccounts } from '../accounts/registry'
import { ApiError, createTransfer, retryTransfer } from '../api/client'
import type { DescribedError } from '../api/errors'
import { FUNDING_ACCOUNT_ID, SETTLEMENT_ACCOUNT_ID } from '../api/seededAccounts'
import type { TransferResponse } from '../api/types'
import { Receipt } from '../components/Receipt'
import { ErrorBanner } from '../components/ErrorBanner'
import { Tabs } from '../components/Tabs'
import { clearPendingTransfer, loadPendingTransfer, mintIdempotencyKey, savePendingTransfer, type PendingTransfer } from '../api/idempotency'
import { formatMinorUnits, parseAmountToMinorUnits } from '../money/money'
import type { Currency } from '../money/money'
import { truncateId } from '../identity/identity'

interface Props {
  ownerId: string
  onSwitchIdentity: () => void
}

type Mode = 'transfer' | 'deposit' | 'withdraw'
type Step = 'form' | 'confirm' | 'receipt'

const MODES: { id: Mode; label: string }[] = [
  { id: 'transfer', label: 'Transfer' },
  { id: 'deposit', label: 'Deposit' },
  { id: 'withdraw', label: 'Withdraw' },
]

const CURRENCY: Currency = 'USD'

/**
 * One screen, three tabs -- Transfer, Deposit, Withdraw -- because all three are one
 * `POST /transfers` and pretending otherwise would mean three near-identical forms
 * (docs/web-ui-plan.md §5.4). The two SYSTEM constants are hard-coded here, mirroring
 * `seeded_accounts.py`, because there is no discovery endpoint.
 */
export function MoveMoneyScreen({ ownerId, onSwitchIdentity }: Props) {
  const [mode, setMode] = useState<Mode>('transfer')
  const [step, setStep] = useState<Step>('form')
  const [myAccountId, setMyAccountId] = useState('')
  const [counterpartyId, setCounterpartyId] = useState('')
  const [amountInput, setAmountInput] = useState('')
  const [amountError, setAmountError] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingTransfer | null>(null)
  const [interrupted, setInterrupted] = useState<PendingTransfer | null>(() => loadPendingTransfer())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<DescribedError | null>(null)
  const [receipt, setReceipt] = useState<TransferResponse | null>(null)

  const myAccounts = listRegisteredAccounts(ownerId)

  function sourceIdFor(mode: Mode): string {
    if (mode === 'deposit') return FUNDING_ACCOUNT_ID
    return myAccountId
  }
  function destinationIdFor(mode: Mode): string {
    if (mode === 'withdraw') return SETTLEMENT_ACCOUNT_ID
    if (mode === 'deposit') return myAccountId
    return counterpartyId
  }

  function resetForm() {
    setStep('form')
    setAmountInput('')
    setAmountError(null)
    setError(null)
    setReceipt(null)
    setPending(null)
  }

  function review() {
    const parsed = parseAmountToMinorUnits(amountInput, CURRENCY)
    if (!parsed.ok) {
      setAmountError(parsed.error)
      return
    }
    const source = sourceIdFor(mode)
    const destination = destinationIdFor(mode)
    if (!source || !destination) {
      setAmountError('Both accounts are required.')
      return
    }
    setAmountError(null)
    const key = mintIdempotencyKey()
    const body = {
      source_account_id: source,
      destination_account_id: destination,
      amount: parsed.value,
      currency: CURRENCY,
    }
    const pendingTransfer: PendingTransfer = { key, body, callerId: ownerId, createdAt: new Date().toISOString() }
    savePendingTransfer(pendingTransfer)
    setPending(pendingTransfer)
    setStep('confirm')
  }

  async function confirm() {
    if (!pending) return
    setSubmitting(true)
    setError(null)
    try {
      const transfer = await createTransfer(pending.body, { callerId: pending.callerId, idempotencyKey: pending.key })
      clearPendingTransfer()
      setReceipt(transfer)
      setStep('receipt')
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setSubmitting(false)
    }
  }

  async function resumeInterrupted() {
    if (!interrupted) return
    setSubmitting(true)
    setError(null)
    try {
      const transfer = await retryTransfer(interrupted.body, { callerId: interrupted.callerId, idempotencyKey: interrupted.key })
      clearPendingTransfer()
      setInterrupted(null)
      setReceipt(transfer)
      setStep('receipt')
    } catch (err) {
      if (err instanceof ApiError) setError(err.described)
      else throw err
    } finally {
      setSubmitting(false)
    }
  }

  function discardInterrupted() {
    clearPendingTransfer()
    setInterrupted(null)
  }

  function editForm() {
    clearPendingTransfer()
    setPending(null)
    setStep('form')
  }

  const parsedPreview = amountInput ? parseAmountToMinorUnits(amountInput, CURRENCY) : null

  return (
    <div className="stack">
      <h2 className="screen-title">Move money</h2>

      {interrupted && (
        <div className="banner banner--info stack">
          <div>
            A transfer was in flight when this page last closed. Retry it? It carries the same retry
            code, so if it already posted, you&apos;ll just see the original.
          </div>
          <div className="row">
            <button className="btn-primary" disabled={submitting} onClick={resumeInterrupted}>
              Retry
            </button>
            <button className="btn-ghost" onClick={discardInterrupted}>
              Discard
            </button>
          </div>
        </div>
      )}

      {step === 'receipt' && receipt ? (
        <div className="stack">
          <Receipt transfer={receipt} />
          <button className="btn-ghost" onClick={resetForm}>
            New movement
          </button>
        </div>
      ) : (
        <>
          <Tabs
            tabs={MODES.map((m) => ({ id: m.id, label: m.label }))}
            activeId={mode}
            onChange={(id) => {
              setMode(id as Mode)
              resetForm()
            }}
          />

          {step === 'form' && (
            <div className="card stack">
              {mode !== 'deposit' && (
                <div className="field">
                  <label>{mode === 'transfer' ? 'From (my account)' : 'From (my account)'}</label>
                  <select value={myAccountId} onChange={(e) => setMyAccountId(e.target.value)}>
                    <option value="">Select an account…</option>
                    {myAccounts.map((a) => (
                      <option key={a.accountId} value={a.accountId}>
                        {truncateId(a.accountId)} · {a.purpose ?? '?'} · {a.currency ?? '?'}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {mode === 'transfer' && (
                <div className="field">
                  <label>To (another customer&apos;s account)</label>
                  <input value={counterpartyId} onChange={(e) => setCounterpartyId(e.target.value)} placeholder="account uuid" />
                </div>
              )}

              {mode === 'deposit' && (
                <div className="field">
                  <label>To (my account)</label>
                  <select value={myAccountId} onChange={(e) => setMyAccountId(e.target.value)}>
                    <option value="">Select an account…</option>
                    {myAccounts.map((a) => (
                      <option key={a.accountId} value={a.accountId}>
                        {truncateId(a.accountId)} · {a.purpose ?? '?'} · {a.currency ?? '?'}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="field">
                <label>Amount (USD)</label>
                <input value={amountInput} onChange={(e) => setAmountInput(e.target.value)} placeholder="0.00" />
                {amountError && <div className="help-text" style={{ color: 'var(--negative)' }}>{amountError}</div>}
                {!amountError && parsedPreview?.ok && (
                  <div className="help-text">{formatMinorUnits(parsedPreview.value, CURRENCY)}</div>
                )}
              </div>

              <div>
                <button className="btn-primary" onClick={review}>
                  Review
                </button>
              </div>
            </div>
          )}

          {step === 'confirm' && pending && (
            <div className="card stack">
              <div>
                Move <strong className="amount tabular">{formatMinorUnits(pending.body.amount, CURRENCY)}</strong> from{' '}
                <span className="mono">{truncateId(pending.body.source_account_id)}</span> to{' '}
                <span className="mono">{truncateId(pending.body.destination_account_id)}</span>
              </div>
              <div className="help-text">
                Safe to wait — this carries a retry code, so a timeout won&apos;t post it twice.
              </div>
              <div className="row">
                <button className="btn-primary" disabled={submitting} onClick={confirm}>
                  {submitting ? 'Confirming…' : 'Confirm'}
                </button>
                <button className="btn-ghost" onClick={editForm}>
                  Edit
                </button>
              </div>
            </div>
          )}

          {error && (
            <ErrorBanner
              error={error}
              onRetry={confirm}
              onSwitchIdentity={onSwitchIdentity}
              onStartOver={editForm}
            />
          )}
        </>
      )}
    </div>
  )
}
