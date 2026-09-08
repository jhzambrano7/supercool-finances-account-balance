import type { TransferResponse } from '../api/types'
import { formatMinorUnits, formatSignedEntry } from '../money/money'
import type { Currency } from '../money/money'

interface Props {
  transfer: TransferResponse
  /** A reversal *is* a `TransferResponse` (I7: a compensating transfer, never a mutation) --
   * this only changes the opening line, not the shape below it. */
  heading?: string
}

/**
 * Rendered from the `201` body -- a full `TransferResponseDto` (§5.5). No "new balance" is shown,
 * and no fake one is computed: the transfer response carries no balance, so this must not invent one.
 */
export function Receipt({ transfer, heading = 'Transfer posted.' }: Props) {
  const currency = transfer.currency as Currency
  return (
    <div className="card stack">
      <div>{heading}</div>
      <div className="amount-display amount tabular">{formatMinorUnits(transfer.amount, currency)}</div>
      <table>
        <tbody>
          <tr>
            <th>Transfer id</th>
            <td className="mono">{transfer.transfer_id}</td>
          </tr>
          <tr>
            <th>From</th>
            <td className="mono">{transfer.source_account_id}</td>
          </tr>
          <tr>
            <th>To</th>
            <td className="mono">{transfer.destination_account_id}</td>
          </tr>
          <tr>
            <th>Requested by</th>
            <td className="mono">{transfer.requested_by}</td>
          </tr>
          <tr>
            <th>Occurred at</th>
            <td>{new Date(transfer.occurred_at).toLocaleString()}</td>
          </tr>
        </tbody>
      </table>

      <div>
        <div className="section-heading" style={{ fontSize: 15, fontWeight: 500, marginBottom: 8 }}>
          Entries
        </div>
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Direction</th>
              <th>Amount</th>
            </tr>
          </thead>
          <tbody>
            {transfer.entries.map((entry) => (
              <tr key={entry.entry_id}>
                <td className="mono">{entry.account_id}</td>
                <td>{entry.direction}</td>
                <td className={`amount tabular ${entry.direction === 'DEBIT' ? 'amount-debit' : 'amount-credit'}`}>
                  {formatSignedEntry(entry.amount, currency, entry.direction)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="help-text" style={{ marginTop: 8 }}>
          These two legs net to zero — the platform&apos;s central invariant, made visible.
        </div>
      </div>
    </div>
  )
}
