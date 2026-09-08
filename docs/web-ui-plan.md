# Web UI Plan — SuperCool Finances Account Balance

A plan, not an implementation. No code exists for any of this. The goal of the surface it describes
is that a reviewer or an operator can exercise the platform in a browser — open an account, move
money, see what the ledger did — without reaching for `curl` and without memorising a UUID.

Ease of use over sophistication. Nothing here is a consumer product.

This document states three kinds of thing, and marks which is which throughout:
**decided** (I am choosing this), **assumed** (I am proceeding on this, and it may be wrong),
**blocked** (the UI cannot ship this until the backend does something first).

---

## 1. What I verified, and where the brief I was given is stale

Everything below was read from the code and specs on this branch, not taken on trust. Four items
came back different from the brief.

### 1.1 Confirmed as described

| Claim | Verified against |
| --- | --- |
| `POST /accounts` body `{owner_id, purpose, currency}`; `201` first open, `200` on natural-key retry, `409` on a genuine race, `422` invalid purpose/currency | `adapters/inbound/api/routes.py`, `dtos.py` |
| `purpose` is `CHECKING`/`SAVINGS` only from the API; `SYSTEM` accounts are seed-only | `dtos.py` (`OpenAccountRequestDto` has no `account_type`, AO1) |
| `POST /transfers` body `{source_account_id, destination_account_id, amount, currency}` + `X-Caller-Id` + `Idempotency-Key` | `transfer_routes.py` |
| `X-Caller-Id` is the entire auth mechanism; missing or non-UUID → `401` before anything else | `adapters/inbound/api/auth.py` (T2) |
| An idempotent replay returns `201`, deliberately indistinguishable from the original | `transfer_routes.py` comment, transfer spec T4 |
| Same key + different body → `409`; caller doesn't own the debited leg → `403` | `transfer_routes.py` `_FORBIDDEN_ERRORS`, `IdempotencyConflictError` |
| `amount` is an integer in minor units; `Currency` carries no exponent, on purpose, and pushes rendering to the client | `shared/domain/money.py` docstrings, verbatim |
| Deposit/withdraw are `POST /transfers` with a seeded `SYSTEM` leg; ids are constants with no discovery endpoint | `adapters/config/seeded_accounts.py`, transfer spec "Known gap" |
| No movement-history endpoint, no balance-read endpoint, no account-list endpoint | `app.py` mounts exactly two routers; both expose exactly one `POST` each |
| Reversal is not on this branch | `openspec/specs/revert/spec.md` does not exist |

The seeded ids, for the record:

```
FUNDING_ACCOUNT_ID     = 00000000-0000-0000-0000-000000000001   (USD)
SETTLEMENT_ACCOUNT_ID  = 00000000-0000-0000-0000-000000000002   (USD)
PLATFORM_OWNER_ID      = 00000000-0000-0000-0000-000000000000   (nil UUID)
```

### 1.2 Stale: the closed currency enum is decided, **not built**

The brief says "currency is being narrowed to a closed enum: `USD`, `MXN`, `COP`". That is a
decision in `openspec/specs/account-balance/spec.md`, and the spec itself is explicit that it is
**"Specified, not yet built"** (line 22). `src/modules/shared/domain/money.py` still validates with
`^[A-Z]{3}$` and nothing else. Both seeded `SYSTEM` accounts are USD.

The consequence for the UI is not cosmetic. Today `POST /accounts` accepts `MXN`, `COP`, `EUR`, and
`ZZZ` — it returns `201` for all of them — and every subsequent deposit into that account returns
`422 CurrencyMismatchError`, permanently, because the only `FUNDING` account is USD. The spec records
this as reproduced against the running service.

**Decided:** the currency selector is hard-coded to `USD` only, with the other options present but
disabled and labelled *"awaiting per-currency funding accounts"*. A UI that offers a currency the
platform cannot fund is a UI that manufactures dead accounts. The selector opens up in Phase 5, when
the enum ships **and** the matching `FUNDING`/`SETTLEMENT` rows are seeded — the spec is emphatic
that those are two coordinated steps, never one.

### 1.3 Stale: the residual-`DomainError` status is `400`, not `500`

The transfer spec's error table says *"any other `DomainError` reaching this endpoint | 500"*.
`transfer_routes.py` maps it to `400`, and so does `routes.py`, and
`docs/coding-conventions.md` has a rule titled **"Domain and application errors default to `400`,
not `500`"**. Two of three sources agree with the code; the spec table row is the stale one.

The UI treats `400` as *"the service rejected this on a business rule it didn't expect"* — same
human copy family as `422`, not the same as a crash.

### 1.4 Two different `422` body shapes, and a `422` that should have been a `401`

`Idempotency-Key` is declared as `Header(alias="Idempotency-Key")` with **no default**, so FastAPI
itself rejects a missing header as a validation error before the route body runs. That means:

- **Header entirely missing** → `422` with `detail` as a **list** of `{loc, msg, type}` objects
  (FastAPI's validation shape).
- **Header present but blank/whitespace** → `422` with `detail` as a **string**
  (`InvalidIdempotencyKeyError`, via `HTTPException`).

`X-Caller-Id` deliberately avoids this — `auth.py` declares it `default=None` precisely so it can
raise `401` itself rather than letting FastAPI's `422` win. The comment says so.

**Consequence for the UI (decided):** the error mapper must branch on `typeof detail`. A UI that
does `String(body.detail)` renders `[object Object]` to the operator on this one path. It is the
cheapest bug in this whole plan to avoid and the most embarrassing to ship.

**Not verified:** whether a request missing *both* headers returns `401` or `422` depends on
FastAPI's dependency-solving order. I did not run the service to find out, and the UI does not care —
it always sends both. Flagging it rather than asserting it.

### 1.5 Blocking, and not in the brief: there is no CORS middleware

`create_app()` in `src/modules/shared/adapters/inbound/api/app.py` mounts two routers and a
lifespan. No `CORSMiddleware`. A browser page served from any other origin cannot read a response
from this API — the failure surfaces in the browser as a generic network error with no status code,
which is the single worst thing to hand a reviewer.

**Decided, and it requires no backend change:** the front-end is served by a dev server that
**proxies `/accounts` and `/transfers` to the API**, so the browser only ever makes same-origin
requests. Vite's `server.proxy` does this in four lines of config. This is deliberately chosen over
asking for `CORSMiddleware`, because the ask would be a production-affecting change to satisfy a
demonstration surface.

For a deployed (non-`localhost`) demo, this becomes a real decision: either serve the built static
files from the same origin as the API (a reverse proxy in front of both), or the backend adds CORS.
Recommend the former. Out of scope for this plan beyond naming it.

### 1.6 `POST /accounts` is unauthenticated

`routes.py` takes no `X-Caller-Id` and does not depend on `resolve_caller_id`. Anyone can open an
account for any `owner_id`. This is consistent with AO1's reasoning (opening has no ownership check
to make) but it is worth stating, because the UI is about to present account-opening and transfers
side by side as if they had the same identity model, and they do not.

**Decided:** the open-account form binds `owner_id` to the currently selected identity and shows it
read-only, with a small "open for a different owner" escape hatch. Not a security control — the API
has none here — just a way to stop the operator creating accounts they then cannot transact on.

---

## 2. Identity, in a UI with no login

`X-Caller-Id` is a raw owner UUID in a header. There is no login, no token, no session, and nothing
to log out of. The UI must not pretend otherwise — a fake login screen over a header would be a
lie that costs the reviewer time.

**Decided: a persistent identity bar, not a login screen.** A fixed bar across the top of every
screen, always visible, showing:

- a deterministic colour swatch + two-letter glyph derived from the UUID (so two identities are
  distinguishable at a glance without reading 36 hex characters),
- the UUID, truncated to `a1b2c3d4…9f0e` with click-to-copy and hover-to-reveal-in-full,
- a `Switch` control opening the identity list,
- a small label: **"Simulated identity — sent as `X-Caller-Id`. There is no authentication."**

That label stays on screen permanently. It is the honest thing and it is also the thing that makes
a `403` comprehensible three clicks later.

**Identity store (decided):** `localStorage`, key `scf.identities`, a list of
`{id: uuid, label: string}`, plus `scf.activeIdentityId`. Actions: **New identity** (mints
`crypto.randomUUID()`, labelled "Person 1", "Person 2", … — renameable), **Paste identity** (accepts
a UUID typed or pasted in, for reproducing a `curl` session or a colleague's account), **Switch**,
**Forget**.

Two identities are seeded on first run, because *every interesting flow in this platform needs two
people*: a customer-to-customer transfer, and a `403` you can actually trigger on purpose.

**Assumed:** owner ids are free-form UUIDs with no registration step — nothing in the backend
validates that an `owner_id` corresponds to anything. Verified for `POST /accounts` (no lookup);
inferred for transfers (ownership is checked against the account row's `owner_id`, so an unknown
caller simply fails authorization rather than being rejected as unknown).

**Explicitly not built:** an operator/admin identity. Reversal is the only operator-authorized
operation (PRD §7.1) and it is not on this branch. When it lands, the identity bar grows a role, and
not before — a role selector with one role is furniture.

---

## 3. Money: the minor-units rule

This is the part of the plan most likely to silently corrupt money, so it gets a module of its own
with unit tests, and every other module is forbidden from doing arithmetic on an amount.

The domain is explicit: `Money` is an integer count of minor units, `Currency` deliberately carries
no exponent, and rendering a human decimal is *"a presentation concern that belongs to the inbound
adapter, not to the domain."* The API does not push that to the inbound adapter today — it returns
the raw integer — so it lands here.

### 3.1 The exponent table lives in the front-end, in exactly one place

```ts
// Minor-unit exponents. The API does not tell us these; ISO 4217 does.
const MINOR_UNIT_EXPONENT = { USD: 2, MXN: 2, COP: 2 } as const;
```

**Open question for the backend owner, flagged not decided:** ISO 4217 assigns `COP` an exponent of
**2** (centavos), but Colombian pesos are quoted, priced and settled in whole units in practice, and
a UI showing `$ 1.000.000,00 COP` reads as wrong to anyone Colombian. The table above follows ISO
4217 because the domain's own docstring calls `Currency` *"an ISO 4217 alphabetic code"* and
consistency with the backend's stated reference beats local convention when the two disagree.
If the answer is 0, this is a one-line change and a test fixture. **Do not let this be decided by
whoever writes the component.** It is worth thirty seconds of a backend owner's attention now, and
a reconciliation incident later if it isn't.

`MXN` and `USD` are unambiguous at 2.

### 3.2 Parsing (human decimal → integer minor units)

**Decided: string arithmetic only. `parseFloat(x) * 100` never appears in this codebase.**
`19.99 * 100` is `1998.9999999999998` in IEEE-754; `Math.round` hides it for small numbers and stops
hiding it eventually. The domain refuses to construct `Money` from a float for exactly this reason,
and the front-end that feeds it must hold the same line.

The rule:

1. Strip grouping separators and whitespace; accept `.` or `,` as the decimal separator (accept both,
   because operators paste from everywhere); reject a string containing more than one.
2. Split into integer and fraction parts as **strings**.
3. If the fraction has **more digits than the exponent, reject** — do not round. `10.999 USD` is a
   typo or a misunderstanding, and silently posting `10.99` (or `11.00`) is precisely the class of
   quiet corruption this whole ledger exists to prevent. Inline message:
   *"USD amounts have at most 2 decimal places."*
4. Right-pad the fraction to the exponent with zeros.
5. `BigInt(integerPart + paddedFraction)`, then range-check and hand the API a plain integer.

Empty fraction, leading `.`, a lone `-`, and `+` are all rejected with specific inline messages
rather than a generic "invalid amount".

**Zero and negatives are not the front-end's rule to enforce** — `NonPositiveAmountError` is the
domain's, and it maps to `422`. The UI *may* disable the submit button on a non-positive amount as a
convenience, but it must still render the backend's `422` correctly if it ever arrives. The front-end
never becomes the authority on a business rule; it is allowed to be a shortcut to the same answer.

### 3.3 Formatting (integer minor units → human decimal)

Also string arithmetic: sign, then left-pad the digits to at least `exponent + 1`, then slice at
`length - exponent`. `Intl.NumberFormat` is used **only** to resolve the locale's grouping and
decimal separators and the currency symbol — never to do the division. Amounts are always rendered
with the currency code adjacent (`1,234.56 USD`), never a bare `$`, because three of the platform's
currencies use `$`.

**Decided:** amounts in app state are stored exactly as the API sends them — the integer, in a
field named `amountMinor`, never `amount`. A formatted string is produced at render time and thrown
away. There is no path by which a display string re-enters a request body.

### 3.4 One more display rule

Entries carry a `direction` (`DEBIT`/`CREDIT`) and a positive `amount`; the sign is in the direction,
not in the number (`Entry.signed_amount` is a domain concept the API does not expose). The receipt
renders `−1,234.56 USD` for a debit and `+1,234.56 USD` for a credit, with the sign derived from
`direction`. Getting this backwards makes a correct ledger look broken.

---

## 4. Idempotency keys, from the client side

`Idempotency-Key` is client-generated with no format imposed (max 255 chars, non-blank). The whole
mechanism is worthless if the client mints a fresh key per attempt — that is a duplicate transfer
with extra steps.

**Decided rules:**

1. **A key is minted per *intent*, not per *attempt*.** `crypto.randomUUID()` is called once, at the
   moment the user opens the confirmation step for a filled-in form — before the first request, not
   inside the request function.
2. **The key is held with the pending request**, in a `localStorage` entry
   `scf.pendingTransfer` = `{key, body, callerId, createdAt}`, written *before* the first `fetch`.
   Surviving a tab refresh mid-request is the point: it turns "did that go through?" into a question
   the UI can answer.
3. **Every retry reuses the key.** Automatic retry on network failure and on `5xx` only — never on a
   `4xx`, which is a decided answer, not a transient one. Two retries, 1s then 3s, then stop and hand
   it to the human with a **Retry** button that *still* reuses the same key.
4. **Editing any field of the form discards the key** and mints a new one on the next confirm. A
   changed body under the same key is the `409` case, and the UI's job is to make that unreachable by
   accident.
5. **On success, the pending entry is cleared.** On `409`, it is cleared and the user is told why.

**A consequence worth stating rather than hiding:** a replay returns `201` and is deliberately
indistinguishable from the original creation (T4). The success screen therefore never says
*"Transfer created"* — it says *"Transfer posted"* and shows the `transfer_id` and `occurred_at`.
If the operator retried something that had already gone through, the honest answer is *this is the
transfer*, which is exactly what they get. The UI must not invent a "this was a replay" badge it has
no way to compute.

---

## 5. Screens and flows

Five screens, one of them blocked, plus a global identity bar. Flat navigation — no nesting, no
router beyond a top-level tab switch.

### 5.1 Identity bar (global) — Phase 1

Described in §2. Present on every screen. Not dismissible.

### 5.2 Open account — Phase 1

Fields: `owner_id` (bound to active identity, read-only, with an escape hatch), `purpose`
(`CHECKING` / `SAVINGS` radio), `currency` (`USD`; `MXN`/`COP` visible and disabled per §1.2).

Responses, and what the human sees:

- **`201`** — *"Account opened."* Shows the account card: id (click-to-copy), purpose, currency,
  balance. The id is added to the local account registry (§5.3).
- **`200`** — *"You already have a CHECKING account in USD. Here it is."* Same card. This is the
  natural-key retry, and it is a **normal, useful** outcome, not an error — the operator uses it
  deliberately as a "look up my account" move. Styling matches `201`, copy differs.
- **`409`** — *"Two requests raced to open this account. Try again — the second attempt will find
  the one that won."* With a **Try again** button that re-submits identically. This is AO4's genuine
  race, and the recovery really is just "retry".
- **`422`** — the `detail` string, verbatim, under the offending field. The backend's messages are
  good; do not paraphrase them.

### 5.3 Accounts — Phase 1 (as a client-side registry), Phase 2 (as real data)

**Blocked in its honest form.** There is no endpoint to list a user's accounts, so Phase 1 ships a
knowingly-partial substitute:

A `localStorage` registry, `scf.accounts.<ownerId>` — a list of every account id this browser has
seen for this identity, from an open-account response. Rendered as cards with a permanent banner:

> **This list lives in your browser, not on the server.** The API has no endpoint to list accounts
> yet. Accounts opened elsewhere won't appear here, and if the database is reset these will point at
> rows that no longer exist.

With **Add by id** (paste an account id you got from `curl`) and **Forget** per card.

**Balance is worse.** The only place a balance is returned by any endpoint is the `POST /accounts`
response body. So the Phase-1 refresh-balance action is: **re-`POST /accounts`** with the same
natural key, get a `200`, read `balance` off it.

That works, it is safe (the endpoint is idempotent by natural key — that is `AO3`'s whole design),
and it is a hack. It is documented as one in the UI: the refresh control is labelled
**"Refresh (via account-open replay)"** with a tooltip explaining why. It requires knowing
`(owner, purpose, currency)`, which the registry stores, and it cannot refresh an account added by
raw id. Deleted in Phase 2, in one commit, when `GET /accounts/{id}` exists.

### 5.4 Move money — Phase 1

One screen, three modes as tabs — **Transfer**, **Deposit**, **Withdraw** — because all three are
one `POST /transfers` and pretending otherwise would mean three near-identical forms.

| Mode | `source_account_id` | `destination_account_id` |
| --- | --- | --- |
| Transfer | picked from my accounts | pasted (another customer's id) |
| Deposit | `00000000-…-0001` (fixed, hidden) | picked from my accounts |
| Withdraw | picked from my accounts | `00000000-…-0002` (fixed, hidden) |

The two constants are hard-coded in the front-end, mirroring `adapters/config/seeded_accounts.py`,
because there is no discovery endpoint. **Stated plainly because it is a real liability:** this is
duplicated knowledge of a platform-internal id in a second codebase, and it is USD-only. It lives in
one file, `src/api/seededAccounts.ts`, with a comment pointing at the backend constant and at the
transfer spec's "Known gap" note, and it is deleted the day `POST /deposits`/`POST /withdrawals`
exist (§6.4). No other file may import those UUIDs.

Flow: fill → **Review** (mints the idempotency key, §4) → a confirmation panel restating
*"Move **1,234.56 USD** from **…a1b2** to **…c3d4**"* with amounts re-formatted from the parsed
integer, not echoed from the input box — so a parsing bug is visible *before* the money moves →
**Confirm** → receipt.

Deliberately no amount presets, no contacts, no recent-recipients. The reviewer's job is to trigger
specific ledger conditions, not to send money quickly.

### 5.5 Receipt — Phase 1

Rendered from the `201` body, which is a full `TransferResponseDto`: `transfer_id`,
`source_account_id`, `destination_account_id`, `amount`, `currency`, `requested_by`, `occurred_at`,
and the `entries` list.

The entries table is the most valuable thing on this screen and the reason it is not just a toast:
it shows the double-entry — a `DEBIT` and a `CREDIT` of equal magnitude — which is the platform's
central invariant made visible. Two rows, signed per §3.4, with a footer asserting they net to zero.

**No "new balance" is shown, and no fake one is computed.** The transfer response carries no
balance, and the front-end must not subtract the amount from a cached figure and present the result
as fact — that number would be wrong the moment anything else posted. The receipt links to the
account card instead, whose balance is fetched (Phase 1: the replay hack; Phase 2: `GET /accounts/{id}`).

### 5.6 Movement history — **BLOCKED**, Phase 3

There is no endpoint. `openspec/specs/transfer/spec.md` lists *"Reading balance or movement history"*
under **Out of Scope (explicitly, not by omission)**. The rows exist (`transfers`, `entries`); nothing
serves them.

The UI ships a placeholder screen that says exactly that, links to the spec line, and does not
simulate anything. Contract requested in §6.2.

### 5.7 Reversal — **BLOCKED**, Phase 4

Not on this branch: `openspec/specs/revert/spec.md` does not exist here, and the transfer spec
sequences `revert` as the next PR in the chain because it reads `Transfer` rows this slice creates.
PRD §7.1 also makes reversal **operator-authorized, not customer-initiated**, so this screen belongs
behind an operator identity the UI does not have yet (§2).

Placeholder screen, same honesty as §5.6. Contract sketched in §6.3, deliberately thin — that spec
is someone else's and this document should not pre-empt it.

---

## 6. The API gaps, as well-formed requests

Four things the UI needs and the backend does not expose. Each is stated as the *minimum* contract
that unblocks a screen, consistent with what is already visible in the codebase — same DTO naming,
same error-mapping discipline, same `X-Caller-Id` authorization, same `Criteria` extension point
(`docs/coding-conventions.md`: a new lookup is a new `Criteria` subtype, never a new port method).

None of these are redesigns. Where a shape already exists (`AccountResponseDto`), it is reused as-is.

### 6.1 Read an account (unblocks balance, kills the replay hack)

```
GET /accounts/{account_id}
Headers: X-Caller-Id: <owner uuid>

200 → AccountResponseDto            (the existing shape, unchanged)
403 → caller is not the account's owner        (AccountOwnershipError)
404 → no such account                          (AccountNotFoundError)
401 → missing/malformed X-Caller-Id
```

Repository side: `FindAccountByAccountId` already exists in the conventions doc's own example.
Authorization: the caller must own it — stricter than transfers' rule and correct here, since this
returns a balance. A `SYSTEM` account id returns `403` (no customer owns it), which also keeps the
"a `SYSTEM` account has no balance" requirement untouched.

### 6.1b List my accounts (kills the localStorage registry)

```
GET /accounts
Headers: X-Caller-Id: <owner uuid>

200 → { "items": [AccountResponseDto, ...] }
401 → missing/malformed X-Caller-Id
```

No query parameters: the owner is the caller. No pagination — a customer's account count is bounded
by `(purpose × currency)` and the natural key already forbids duplicates, so this cannot grow
unboundedly. New criteria: `FindAccountsByOwner`, returning a list (the port's `find` returns one
today; a `find_all`-shaped sibling or a list-returning criteria is the backend's call, not mine).

Wrapping in `{items: [...]}` rather than returning a bare array, so a `next_cursor` can be added
later without a breaking change.

### 6.2 Movement history — the one that actually needs design

```
GET /accounts/{account_id}/movements?limit=<1..100>&cursor=<opaque>
Headers: X-Caller-Id: <owner uuid>

200 → {
  "items": [
    {
      "entry_id":                 "uuid",
      "transfer_id":              "uuid",
      "direction":                "DEBIT" | "CREDIT",
      "amount":                   1234,          // minor units, positive
      "currency":                 "USD",
      "counterparty_account_id":  "uuid",
      "requested_by":             "uuid",
      "occurred_at":              "2026-09-07T12:34:56.789Z"
    }
  ],
  "next_cursor": "opaque-string" | null
}
403 → caller is not the account's owner
404 → no such account
401 → missing/malformed X-Caller-Id
```

Four deliberate choices, each with its reason:

- **Entries, not transfers.** A statement is what happened *to this account*. Returning transfers
  would make the client work out which leg was theirs and what the sign was — exactly the derivation
  §3.4 exists to keep in one place. `direction` + a positive `amount` mirrors `EntryResponseDto`,
  which already exists.
- **Cursor pagination, not offset.** The ledger is append-only and read newest-first; an offset
  shifts under the reader every time anything posts. The cursor is opaque to the client and encodes
  `(occurred_at, entry_id)` descending — `entry_id` breaking ties, because two entries of the same
  transfer share an `occurred_at`. Default `limit` 25, max 100.
- **`counterparty_account_id`, not a name.** There is no name anywhere in this service, and
  inventing one would need a customer directory that does not exist.
- **No `balance_after`.** A running balance is not stored, and computing one per row means summing
  the account's history on the request path — the exact cost the `SYSTEM`-balance requirement was
  revised to remove. If a running balance is wanted later, that spec's own answer applies:
  snapshots, not a live `SUM`. The UI does not need it to be useful.

This endpoint reads a `USER` account only. `SYSTEM` movement history is an operator concern and is
not requested here.

### 6.3 Reversal — minimum shape, not a design

Deferred to `openspec/specs/revert/spec.md`, wherever it lands. The UI needs only this much to build
against it:

```
POST /transfers/{transfer_id}/reversals
Headers: X-Caller-Id (operator), Idempotency-Key
Body:    {} or { "reason": "..." }

201 → TransferResponseDto   (the reversal is itself a transfer — PRD I7, "corrections happen by
                             compensating entry, never by mutation")
409 → already reversed  /  same key, different body
403 → caller lacks operator authority (PRD §7.1)
404 → no such transfer
```

Nested under the original transfer because a reversal is meaningless without one. Returning a
`TransferResponseDto` means the existing receipt screen renders it with no new component — including
the case where the recipient goes negative (PRD §7.3), which the receipt shows without complaint
because a negative balance there is by design.

### 6.4 Explicit deposit / withdraw

Already decided and planned in the transfer spec's "Known gap" note; not designed. The UI's ask is
just that it happens, so the hard-coded `SYSTEM` UUIDs (§5.4) can be deleted:

```
POST /deposits      { account_id, amount, currency }
POST /withdrawals   { account_id, amount, currency }
Headers: X-Caller-Id, Idempotency-Key
201 → TransferResponseDto ; same status/error table as POST /transfers
```

The endpoint resolves the `FUNDING`/`SETTLEMENT` account *for the target account's currency* — the
spec's own `FindAccountByPurposeAndCurrency` criteria. Front-end cost of switching: one function in
`src/api/`, and one deleted file. The three-tab UI does not change.

---

## 7. Error handling, as a table of human stories

One mapper, one place, `src/api/errors.ts`. Rules: branch on `typeof detail` (§1.4); never render a
raw status code to the operator; never swallow the backend's `detail` string, which is uniformly
good.

| What happened | HTTP | What the human reads | Recovery offered |
| --- | --- | --- | --- |
| Not the owner of the debited account | `403` | **"That account isn't yours."** You're acting as *Person 2* (`…c3d4`), and this transfer takes money out of an account owned by someone else. Only the owner of the account being debited can move money out of it. | **Switch identity** (opens the picker), or edit the source account |
| System-to-system movement | `403` | **"Both accounts are platform accounts."** No customer can request this movement. | Change one leg |
| Key reused with a different body | `409` | **"This confirmation was already used for a different transfer."** The retry code on this attempt belongs to a transfer with different details, so we stopped rather than post something you didn't intend. | **Start over** — mints a fresh key, keeps the form |
| Account-open race | `409` | **"Two requests raced to open this account."** Retrying will find whichever one won. | **Try again** |
| Insufficient funds | `422` | Backend `detail`, verbatim, plus: *"Balances can't go negative from a customer action."* | Edit amount; link to Deposit |
| Currency mismatch | `422` | Backend `detail`, plus: *"Both accounts must hold the same currency — this service doesn't convert."* If a `SYSTEM` leg is involved, add: *"Deposits and withdrawals are USD-only today."* | Edit; link to §1.2 note |
| Non-positive amount / bad currency / blank key | `422` (string `detail`) | Backend `detail` under the offending field | Inline correction |
| `Idempotency-Key` header missing | `422` (**list** `detail`) | **"The app didn't send a retry code."** This is a bug in this page, not something you did. | **Report** (copies the request id + body to clipboard) |
| Missing/malformed `X-Caller-Id` | `401` | **"No identity is selected."** Every money movement needs one — there's no login here, just a simulated caller id. | Opens the identity picker |
| Account not found | `404` | **"No account with that id."** Check for a truncated paste — ids are 36 characters. | Field focus |
| Unexpected domain/application error | `400` | Backend `detail`, plus: *"The service rejected this on a rule the page didn't anticipate."* | **Report** |
| Server fault | `5xx` | **"The service is having trouble."** Your transfer may or may not have posted — retrying is safe, it carries the same retry code. | Auto-retried twice, then **Retry** |
| Network / CORS / server down | *(no status)* | **"Couldn't reach the service."** Check that the API is running and that the dev proxy is configured — a browser can't read this API cross-origin (no CORS middleware). | **Retry** (same key) |

Loading and empty states that carry weight:

- **Submitting a transfer:** button disabled, spinner, and the text *"Safe to wait — this carries a
  retry code, so a timeout won't post it twice."* Reassurance that is actually true.
- **Interrupted transfer found on load** (a `scf.pendingTransfer` survived a refresh): a banner —
  *"A transfer was in flight when this page last closed. Retry it? It carries the same retry code,
  so if it already posted, you'll just see the original."* **Retry** / **Discard**.
- **No accounts yet:** *"Open an account to get started"* with the form inline, not a link to it.
- **No movements** (Phase 3): distinguish *"this account has no movements yet"* from *"we couldn't
  load them"*. Two different states, two different messages, never one grey box.

---

## 8. Tech stack

**Recommended: Vite + React + TypeScript. No UI library, no state library, no data-fetching library,
no router.** Four screens, `useState`, `fetch`, and a hand-written `<Tabs>`.

The trade-off, stated once: TypeScript earns its keep here specifically because minor-unit integers,
formatted display strings, and UUIDs are all trivially confusable at runtime and a `type AmountMinor`
brand makes the one dangerous mix-up (§3) a compile error rather than a reconciliation ticket; the
cost is a build step this surface would otherwise not need.

Considered and rejected:

- **A single static HTML file with vanilla JS, no build at all.** Genuinely tempting — smaller than
  the recommendation and it would work. Rejected because the money module and the error mapper both
  deserve unit tests, and untested string arithmetic over money is the one corner where "it's just a
  demo" is not an acceptable answer.
- **Next.js.** No SSR need, no routing need, no API-route need — the API already exists. It would
  add a framework's worth of concepts to four forms.
- **A component library (MUI/Chakra/shadcn).** The visual direction (§9) is small enough to write
  by hand in one stylesheet, and a library would fight it more than help.

**Vitest** for `money.ts` and `errors.ts` — those two only. No component tests, no E2E. If the money
module is right and the error mapper is right, the rest is a form.

Layout: `src/api/` (client, error mapper, seeded constants), `src/money/`, `src/identity/`,
`src/screens/`, `src/components/`. Nothing clever.

**Location:** a `web/` directory at the repo root, with its own `package.json`. Kept out of `src/`,
which is the Python service's, and out of the service's packaging entirely.

---

## 9. Visual direction — based on EFEX

**Source: `https://efex.com/en`.** Confirmed as the right company: the homepage H1 is
*"Business Class Global Treasury"*, subhead *"…collect payments for your sales in the world's most
important markets, transfer money internationally, manage currencies, and optimize your treasury from
a single place."* — multi-currency accounts, FX, US–Mexico corridor, B2B treasury. The old domain
`efexpay.com` 301-redirects there.

**Naming collision, worth recording:** `efex.finance` is a *different, Brazilian* company. Nothing
below came from it.

The values below were read out of the site's compiled stylesheet
(`https://efex.com/_astro/Navbar.4cPNe7VT.css`, ~294KB) fetched directly, because HTML-to-markdown
extraction strips CSS. Every item is marked **[observed]** — it is in that file — or **[adapted]** —
a decision I made for this UI, with the reason. Nothing is presented as observed that wasn't.

### 9.1 Palette

| Token | Hex | Provenance |
| --- | --- | --- |
| `--ink` | `#111111` | **[observed]** primary text and dark surfaces — 158 rules, e.g. `.section-hero-mult-d{background-color:#111}` |
| `--paper` | `#FFFFFF` | **[observed]** 141 rules |
| `--accent` | `#FFF98E` | **[observed]** the primary CTA fill: `.section-hero__button{background:#fff98e}`, hover → `#fff`. A pale yellow, with `--ink` text on it |
| `--link` | `#006BF8` | **[observed]** secondary accent — select highlights, active dropdown, icon fills |
| `--line` | `#EAEAEA` | **[observed]** light border/divider |
| `--muted` | `#A3A3A3` | **[observed]** secondary text |
| `--line-strong` | `#D4D4D4` | **[observed]** |
| `--ink-2` | `#404040` | **[observed]** |
| `--surface-dark` | `#1A1A1A` | **[observed]** |
| `--positive` | `#4ADE80` | **[observed as a value]**, **[inferred as semantic]** — a green appearing 3 times; its role as "success" is my reading, not something the CSS labels |
| `--positive-deep` | `#1E6E46` | same caveat |
| `--warning` | `#FFB40A` | **[observed as a value]**, **[inferred as semantic]** |
| `--negative` | `#C7361F` | **[adapted — not observed]** no error/danger colour was found in the stylesheet. This is a derived red, chosen for AA contrast against `--paper`. Marked so nobody later cites it as EFEX's |

**[observed]** the site uses **no colour CSS custom properties at all** — every colour is hardcoded
per rule. The only variable is `--sans`. **[adapted]** this UI tokenises them anyway; a hardcoded
palette is a marketing-site affordance and a liability in an app with a dark mode question.

**[adapted] how the accent is used here.** On EFEX the yellow is *the* CTA on dark hero blocks. In
this UI it is reserved for **exactly one control per screen** — the primary money-moving action
(*Confirm transfer*, *Open account*). Everything else is `--ink` outline or ghost. A demonstration
tool where three buttons compete for the eye is a tool where somebody clicks the wrong one, and here
the wrong one moves money.

**[adapted] money colour.** Debits `--negative`, credits `--positive-deep`, and **never colour
alone** — the sign (§3.4) and the `DEBIT`/`CREDIT` label carry the meaning; colour reinforces it.
Non-negotiable for a ledger, and it also survives colour-blindness.

### 9.2 Typography

**[observed]** loaded from Google Fonts:
`family=DM+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700;800&display=swap`

**[observed]** the stack, verbatim from the stylesheet:

```css
--sans: "DM Sans", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
```

**[observed]** DM Sans on headings, hero and buttons (`.section-hero__title{font-family:DM Sans;font-weight:400}`);
Inter on some secondary UI text; observed usage clusters at **400** for headings and **500** for
buttons and labels — heavier weights are loaded but their use wasn't confirmed. Headline tracking
`letter-spacing:-.02em`. **[observed]** `text-transform:uppercase` appears **once** in a 294KB
bundle — this brand does not shout.

**[observed]** the hero scales fluidly: `font-size: calc(64px * 100vw / 1440px)` desktop,
`calc(34px * 100vw / 375px)` mobile, over a granular ladder — 12, 13, 14, 15, 16, 17, 18, 20, 22,
24, 26, 28, 30, 32, 34, 36, 40, 44, 48, 52, 56, 58, 64px.

**[adapted] and this is the one place I'm departing from the source on purpose.** That fluid,
23-step scale is a marketing-page technique. In a UI whose main content is tables of ids and amounts
it produces text that changes size as you resize a window, which is unreadable when you're comparing
two figures. Fixed rem, six steps:

| Role | Size / weight |
| --- | --- |
| Screen title | 28px / 500, `-0.02em` |
| Section heading | 20px / 500 |
| Body, form labels | 15px / 400 |
| Secondary, help text | 13px / 400, `--muted` |
| Amounts (display) | 24px / 500, **tabular** |
| Ids, keys, JSON | 13px / 400, mono |

**[adapted, not observed, and load-bearing]** `font-variant-numeric: tabular-nums` on every amount
and every table of numbers, and a monospace face (`ui-monospace, SFMono-Regular, Menlo, monospace`)
for UUIDs and idempotency keys. Proportional digits make two amounts of the same magnitude
misalign, and proportional UUIDs make a truncated paste invisible. EFEX has no reason to care about
either; this UI does.

### 9.3 Shape, spacing, controls

**[observed]** buttons are fully pill-shaped — `border-radius:999px` (35 rules) and `100px` (6).
Cards use a fluid 12–24px radius (`calc(20px*100vw/1440px)` and friends); small chips use 4/6/8/10px;
avatars `50%`. Button padding `12px 24px`.

**[observed]** the primary CTA is solid `#fff98e` with `#111` text, DM Sans 500, no uppercase, hover
to `#fff`. A ghost variant exists — transparent with `border:1px solid rgba(0,0,0,.3)`
(`.navbar-wrapper__get-into`). A dark variant `background:#111;color:#fff;border-radius:100px`
appears in some hero versions — the site runs several hero components (`section-hero-Tr`,
`section-hero-Tm`, `section-hero-mult-d`, `section-heroCIM`), which reads as A/B testing.

**[not confirmed]** input-field styling. I found wrappers (`.input-wrapper`, `.first-input-wrapper`)
but not the `<input>` rules themselves. **[adapted]** inputs here are 8px-radius rectangles, 1px
`--line` border, 40px tall, focus ring `--link` at 2px. Rectangular, not pill — a pill-shaped text
field wastes horizontal space at both ends, and this form's fields hold 36-character UUIDs.

**[adapted] spacing rhythm:** a fixed 8px base — 4 / 8 / 12 / 16 / 24 / 32 / 48. Same reasoning as
the type scale: fixed beats fluid when the content is tabular.

**[adapted] density.** EFEX's marketing pages are airy; I read that from the calc-based gap values
rather than from a rendered screenshot, so treat "airy" as inferred. This UI is **deliberately
denser** — 40px controls, 12px cell padding, 640px max form width. It is an operator surface where
seeing the whole state at once matters more than breathing room. The borrowed part is the *palette,
type and shape language*; the density is mine.

**[adapted] cards:** `--paper` on a `#FAFAFA` page, 1px `--line`, 12px radius, no drop shadow —
EFEX's dark-block-on-light-page contrast doesn't transfer to a screen made of forms, and borders
read more precisely than shadows for tabular content.

### 9.4 Tone of copy

**[observed]** verbatim from the site:

> "Business Class Global Treasury"
> "The level of control and sophistication of large corporations, without the complexity of multiple
> banks or reconciliations, just one click away."
> "See how much you could save with EFEX"
> "Compare, with your own numbers, what your international payments cost you today at a traditional
> bank and what they would cost with EFEX."

**[inferred]** confident, corporate-B2B, aspirational — "punch above your weight" positioning aimed
at SMB and mid-market. Enterprise-treasury register, not casual neobank.

**[adapted]** this UI takes the *register* — plain, precise, unhurried, no exclamation marks, no
emoji — and drops the *salesmanship*, because there is nothing to sell here. The error copy in §7 is
written in that voice: it states what happened, why, and what to do, in complete sentences. The
identity bar's permanent *"Simulated identity — there is no authentication"* is the tonal anchor for
the whole surface. A demonstration tool that oversells itself is a demonstration tool nobody trusts.

---

## 10. Build order

Each phase is shippable and demonstrable on its own. Phase 1 uses **only** what today's API serves.

### Phase 0 — Foundations (no screens)

Vite scaffold with the dev proxy (§1.5); `money.ts` with its exponent table and full unit tests;
`errors.ts` with the §7 table and its `typeof detail` branch, unit-tested; the API client with
identity and idempotency-key injection; the identity store.

Nothing is visible at the end of this phase. It is still the phase that decides whether the rest is
correct.

### Phase 1 — What the API can serve today

Identity bar · Open account (`201`/`200`/`409`/`422`) · Move money, three tabs, with the hard-coded
`SYSTEM` constants · Receipt with the double-entry table · Accounts as a `localStorage` registry with
the honesty banner and the account-open-replay balance refresh · The full error table · The
interrupted-transfer recovery banner.

USD only. Every gap visible in the UI as a labelled placeholder, never as a broken control.

**At the end of Phase 1 a reviewer can:** open two accounts for two identities, deposit into one,
transfer between them, withdraw, watch a `403` by transferring from an account they don't own, watch
a `409` by editing the amount after confirming, and watch an idempotent replay by retrying.
That is the whole point of the surface, and it needs nothing from the backend.

### Phase 2 — Unblocked by §6.1 / §6.1b

Real account list. Real balances. Delete `scf.accounts.*` and the replay hack, and delete the
honesty banner with them.

### Phase 3 — Unblocked by §6.2

Movement history: statement table, cursor paging, empty vs error states, deep-link from a receipt.

### Phase 4 — Unblocked by the `revert` slice

Operator identity in the identity bar. Reversal from a movement row or a receipt, reusing
`TransferResponseDto` rendering. Negative balances rendered plainly, not as an error.

### Phase 5 — Unblocked by the closed `Currency` enum + per-currency `SYSTEM` seeds

Enable `MXN`/`COP` in the selector; revisit the `COP` exponent (§3.1) *before* enabling it. If §6.4
also landed, delete `seededAccounts.ts` and switch the deposit/withdraw tabs to their own endpoints.

---

## 11. Summary: decided / assumed / blocked

**Decided**
Vite + React + TS in `web/`, no libraries · dev-server proxy instead of asking for CORS · persistent
simulated-identity bar, never a fake login · USD-only until per-currency `SYSTEM` accounts exist ·
string-arithmetic money parsing that rejects excess precision rather than rounding · idempotency key
minted per intent, persisted before the first request, reused on every retry, discarded on form edit
· `409`/`403` get written recoveries, not status codes · no fabricated balances anywhere.

**Assumed**
Owner ids need no registration (verified for account-open, inferred for transfers) · the API is
reachable at a single origin the dev server can proxy · a reviewer runs this against a local
`docker compose` PostgreSQL with migrations applied, so the seeded `SYSTEM` accounts exist — if they
don't, deposits `404` and the UI's message should say so.

**Blocked**
Movement history (§6.2) · real balances and account listing (§6.1, §6.1b) · reversal (§6.3, not on
this branch) · explicit deposit/withdraw (§6.4) · `MXN`/`COP` (enum specified, not built; seeds
USD-only).

**Needs a decision from someone other than me**
The `COP` minor-unit exponent (§3.1). Everything else in this document I am willing to be wrong
about cheaply; that one gets expensive.
