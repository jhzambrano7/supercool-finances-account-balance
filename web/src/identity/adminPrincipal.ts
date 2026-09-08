/**
 * The platform's one operator principal (`README.md`, "Reversal is operator-only, simulated by
 * one fixed admin principal"), mirrored here rather than re-typed at each call site -- the same
 * reasoning `admin_principal.py` gives on the backend for hard-coding it once.
 *
 * Displayed on `ReversalScreen` so the demo user knows what to paste into the identity bar --
 * never sent as an override. `X-Caller-Id` for a reversal is always whichever identity is active,
 * same as every other screen; a caller who has not actually switched to this principal correctly
 * gets refused (403) by the real backend.
 */
export const ADMIN_PRINCIPAL_ID = '00000000-0000-0000-0000-000000000003'
