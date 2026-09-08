/**
 * Mirrors `src/modules/account_balance/adapters/config/seeded_accounts.py`.
 *
 * Duplicated knowledge of a platform-internal id, in a second codebase --
 * a real liability, stated plainly (docs/web-ui-plan.md §5.4). It exists
 * only because there is no discovery endpoint yet and no dedicated
 * `POST /deposits` / `POST /withdrawals` (openspec/specs/transfer/spec.md,
 * "Known gap, decided, not yet built"). USD-only, same as the backend seed.
 *
 * No other file may import these constants directly -- go through
 * `depositDestination` is not needed today (the "Move money" screen is the
 * only caller), but if a second caller ever needs these ids, it should read
 * this module, not `seeded_accounts.py`'s literal values copied a second time.
 *
 * Delete this file the day `POST /deposits` / `POST /withdrawals` exist.
 */
export const FUNDING_ACCOUNT_ID = '00000000-0000-0000-0000-000000000001'
export const SETTLEMENT_ACCOUNT_ID = '00000000-0000-0000-0000-000000000002'
export const PLATFORM_OWNER_ID = '00000000-0000-0000-0000-000000000000'
