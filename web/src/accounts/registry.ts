/**
 * Accounts, as a client-side registry (docs/web-ui-plan.md §5.3) --
 * knowingly partial. There is no `GET /accounts` yet, so this is every
 * account id this browser has seen for a given owner, from an
 * open-account response. Gone in Phase 2, in one commit, when that
 * endpoint exists.
 */

export interface RegisteredAccount {
  accountId: string
  /** Unknown for an account added by raw id -- the natural key needed to refresh it via the replay hack isn't known. */
  purpose: 'CHECKING' | 'SAVINGS' | null
  currency: string | null
}

function key(ownerId: string): string {
  return `scf.accounts.${ownerId}`
}

export function listRegisteredAccounts(ownerId: string): RegisteredAccount[] {
  try {
    const raw = localStorage.getItem(key(ownerId))
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeRegisteredAccounts(ownerId: string, accounts: RegisteredAccount[]): void {
  try {
    localStorage.setItem(key(ownerId), JSON.stringify(accounts))
  } catch {
    // best-effort
  }
}

export function registerAccount(ownerId: string, account: RegisteredAccount): void {
  const existing = listRegisteredAccounts(ownerId)
  if (existing.some((a) => a.accountId === account.accountId)) return
  writeRegisteredAccounts(ownerId, [...existing, account])
}

export function forgetAccount(ownerId: string, accountId: string): void {
  const existing = listRegisteredAccounts(ownerId)
  writeRegisteredAccounts(
    ownerId,
    existing.filter((a) => a.accountId !== accountId),
  )
}
