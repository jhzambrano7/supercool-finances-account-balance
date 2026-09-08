import { useEffect, useState } from 'react'
import {
  ApiError,
  createDeposit,
  createTransfer,
  createWithdrawal,
  getAccounts,
  retryDeposit,
  retryTransfer,
  retryWithdrawal,
} from '../api/client'
import type { DescribedError } from '../api/errors'
import {
  clearPendingOperation,
  loadPendingOperation,
  mintIdempotencyKey,
  savePendingOperation,
  type PendingOperation,
} from '../api/idempotency'
import type { AccountResponse, TransferResponse } from '../api/types'
import { Receipt } from '../components/Receipt'
import { ErrorBanner } from '../components/ErrorBanner'
import { Tabs } from '../components/Tabs'
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

async function submit(pending: PendingOperation, headers: { callerId: string; idempotencyKey: string }): Promise<TransferResponse> {
  switch (pending.kind) {
    case 'transfer':
      return createTransfer(pending.body, headers)
    case 'deposit':
      return createDeposit(pending.body, headers)
    case 'withdrawal':
      return createWithdrawal(pending.body, headers)
  }
}

async function resubmit(pending: PendingOperation, headers: { callerId: string; idempotencyKey: string }): Promise<TransferResponse> {
  switch (pending.kind) {
    case 'transfer':
      return retryTransfer(pending.body, headers)
    case 'deposit':
      return retryDeposit(pending.body, headers)
    case 'withdrawal':
      return retryWithdrawal(pending.body, headers)
  }
}

/**
 * One screen, three tabs -- Transfer, Deposit, Withdraw -- backed by three distinct endpoints
 * (`/transfers`, `/deposits`, `/withdrawals`, openspec/specs/transfer/spec.md, "The design,
 * settled"). Deposit/Withdraw no longer need the platform's `FUNDING`/`SETTLEMENT` account id at
 * all -- the backend resolves it -- so this screen only ever asks for the customer's own account.
 */
export function MoveMoneyScreen({ ownerId, onSwitchIdentity }: Props) {
  const [mode, setMode] = useState<Mode>('transfer')
  const [step, setStep] = useState<Step>('form')
  const [myAccountId, setMyAccountId] = useState('')
  const [counterpartyId, setCounterpartyId] = useState('')
  const [amountInput, setAmountInput] = useState('')
  const [amountError, setAmountError] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingOperation | null>(null)
  const [interrupted, setInterrupted] = useState<PendingOperation | null>(() => loadPendingOperation())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<DescribedError | null>(null)
  const [receipt, setReceipt] = useState<TransferResponse | null>(null)

  const [myAccounts, setMyAccounts] = useState<AccountResponse[]>([])

  useEffect(() => {
    let cancelled = false
    getAccounts(ownerId)
      .then((response) => {
        // Unlike an amount (genuinely unknown until submit), a CLOSED account is already a known
        // fact at render time -- it can never be a valid leg for any of the three tabs, so listing
        // it here would just be a dropdown option guaranteed to 422 on every path, not a real choice.
        if (!cancelled) setMyAccounts(response.items.filter((a) => a.status === 'ACTIVE'))
      })
      .catch(() => {
        // Best-effort: an empty dropdown is a visible, honest failure mode here -- the form's
        // own submit path still reports a real error if the caller tries to proceed without one.
      })
    return () => {
      cancelled = true
    }
  }, [ownerId])

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
    if (!myAccountId || (mode === 'transfer' && !counterpartyId)) {
      setAmountError(mode === 'transfer' ? 'Both accounts are required.' : 'An account is required.')
      return
    }
    setAmountError(null)

    const key = mintIdempotencyKey()
    const base = { key, callerId: ownerId, createdAt: new Date().toISOString() }
    const operation: PendingOperation =
      mode === 'transfer'
        ? {
            ...base,
            kind: 'transfer',
            body: {
              source_account_id: myAccountId,
              destination_account_id: counterpartyId,
              amount: parsed.value,
              currency: CURRENCY,
            },
          }
        : mode === 'deposit'
          ? {
              ...base,
              kind: 'deposit',
              body: { destination_account_id: myAccountId, amount: parsed.value, currency: CURRENCY },
            }
          : {
              ...base,
              kind: 'withdrawal',
              body: { source_account_id: myAccountId, amount: parsed.value, currency: CURRENCY },
            }

    savePendingOperation(operation)
    setPending(operation)
    setStep('confirm')
  }

  async function confirm() {
    if (!pending) return
    setSubmitting(true)
    setError(null)
    try {
      const transfer = await submit(pending, { callerId: pending.callerId, idempotencyKey: pending.key })
      clearPendingOperation()
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
      const transfer = await resubmit(interrupted, { callerId: interrupted.callerId, idempotencyKey: interrupted.key })
      clearPendingOperation()
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
    clearPendingOperation()
    setInterrupted(null)
  }

  function editForm() {
    clearPendingOperation()
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
            A {interrupted.kind} was in flight when this page last closed. Retry it? It carries the
            same retry code, so if it already posted, you&apos;ll just see the original.
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
              <div className="field">
                <label>{mode === 'withdraw' ? 'From (my account)' : mode === 'deposit' ? 'To (my account)' : 'From (my account)'}</label>
                <select value={myAccountId} onChange={(e) => setMyAccountId(e.target.value)}>
                  <option value="">Select an account…</option>
                  {myAccounts.map((a) => (
                    <option key={a.account_id} value={a.account_id}>
                      {truncateId(a.account_id)} · {a.purpose} · {a.currency}
                    </option>
                  ))}
                </select>
              </div>

              {mode === 'transfer' && (
                <div className="field">
                  <label>To (another customer&apos;s account)</label>
                  <input value={counterpartyId} onChange={(e) => setCounterpartyId(e.target.value)} placeholder="account uuid" />
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
                {pending.kind === 'transfer' && (
                  <>
                    Move <strong className="amount tabular">{formatMinorUnits(pending.body.amount, CURRENCY)}</strong> from{' '}
                    <span className="mono">{truncateId(pending.body.source_account_id)}</span> to{' '}
                    <span className="mono">{truncateId(pending.body.destination_account_id)}</span>
                  </>
                )}
                {pending.kind === 'deposit' && (
                  <>
                    Deposit <strong className="amount tabular">{formatMinorUnits(pending.body.amount, CURRENCY)}</strong> into{' '}
                    <span className="mono">{truncateId(pending.body.destination_account_id)}</span>
                  </>
                )}
                {pending.kind === 'withdrawal' && (
                  <>
                    Withdraw <strong className="amount tabular">{formatMinorUnits(pending.body.amount, CURRENCY)}</strong> from{' '}
                    <span className="mono">{truncateId(pending.body.source_account_id)}</span>
                  </>
                )}
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
