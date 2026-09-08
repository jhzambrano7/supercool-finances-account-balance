/**
 * The platform's one operator principal (`README.md`, "Reversal is operator-only, simulated by
 * one fixed admin principal"), mirrored here rather than re-typed at each call site -- the same
 * reasoning `admin_principal.py` gives on the backend for hard-coding it once.
 *
 * Deliberately NOT wired through the identity switcher (`identity.ts`/`IdentityBar.tsx`): a
 * reversal's `X-Caller-Id` must be this constant regardless of which identity is active, and the
 * active identity is also who `GET /accounts/{id}/movements` is scoped to -- switching the whole
 * app to "Admin" to reverse something would leave the admin unable to see the very account whose
 * movement they came from (the admin owns no customer accounts). The "Reverse" action overrides
 * the caller id for that one request only.
 */
export const ADMIN_PRINCIPAL_ID = '00000000-0000-0000-0000-000000000003'
