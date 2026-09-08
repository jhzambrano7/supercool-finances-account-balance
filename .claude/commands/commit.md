---
description: Commit staged changes using Conventional Commits, referencing the PRD instead of a ticket
argument-hint: "[optional: extra context about the change]"
---

# commit

Commit the current changes. This project has **no Jira**: traceability comes from
the PRD (`docs/prd.md`), so every commit must say which part of the product
contract it serves.

## 1. Inspect before composing

Run `git status` and `git diff --staged` (plus `git diff` if nothing is staged).
**Never compose a message from assumptions — read the actual diff.**

If nothing is staged, stage the files that belong to the logical change, not
everything present. One commit = one logical change. If the working tree holds
two unrelated changes, make two commits.

## 2. Separate generated session artifacts

These files are regenerated on every turn and would otherwise pollute every
feature diff:

- `docs/ai-transcript.md`
- `docs/.transcript-store.jsonl`
- `docs/decision-log.md`

If they changed **alongside** real work, commit them **separately and last**:

```
docs(session): sync AI transcript and decision log

PRD: n/a — generated compliance artifacts, no product change
```

Never mix them into a feature commit.

## 3. Quality gate

If the project defines one, run the checks before committing:

- `pre-commit run --files <staged files>` when `.pre-commit-config.yaml` exists
- the project test command when the change touches `src/`

If a check fails, **stop and report it**. Do not commit around a failing gate and
do not "fix" it by weakening the check.

## 4. Message format

```
<type>(<scope>): <subject>

<body: what changed and WHY — the reasoning, not a restatement of the diff>

PRD: <§ sections and/or stable IDs>
```

### Types

`feat` `fix` `refactor` `perf` `test` `docs` `build` `ci` `chore`

### Scopes — follow the hexagonal layout

| Scope | Covers |
| --- | --- |
| `domain` | Entities, value objects, domain invariants |
| `application` | Use cases, ports, gateways, services |
| `api` | Inbound HTTP adapters |
| `sql` | Outbound persistence adapters |
| `db` | Alembic migrations, schema |
| `shared` | `src/modules/shared` |
| `infra` | Containers, IaC, deployment |
| `docs` | PRD and project documentation |
| `web` | The `web/` ops console (React) — a demo surface, not held to backend rigor |
| `tooling` | Hooks, scripts, developer workflow |
| `session` | Generated transcript / decision-log artifacts |

### Subject

Imperative mood, lowercase, no trailing period, max ~72 chars.
`enforce non-negative balance on user accounts` — not `enforced` / `enforces`.

## 5. The PRD trailer — this replaces the ticket reference

Every commit ends with a `PRD:` trailer. Prefer the **stable identifiers** over
section numbers: sections get renumbered when new ones are inserted, but goal and
invariant IDs never move.

| Reference | When | Example |
| --- | --- | --- |
| `I1`–`I7` | The change enforces, tests, or alters a domain invariant | `PRD: I2, §4.4` |
| `G1`–`G5` | The change advances a stated goal | `PRD: G3, §6` |
| `§n.n` | Supporting context, or the section is the only relevant anchor | `PRD: §5.1` |
| `n/a — <reason>` | Genuinely unrelated to the product contract | `PRD: n/a — repo tooling` |

`n/a` **requires a reason**. It must be a deliberate statement that the change
carries no product contract, never a silent omission.

Before writing IDs, verify them against `docs/prd.md` — never cite an invariant
from memory. If the change enforces an invariant that the PRD does not yet
define, say so: the PRD needs updating first.

## 6. Rules

- **Never** add `Co-Authored-By` or any AI attribution.
- Never commit secrets, credentials, or `.env` files. Inspect the diff for them.
- Never use `git add -A` blindly, never `--no-verify`, never amend or force-push
  a commit that is already pushed.
- Do not push unless the user asks.

## 7. Examples

```
feat(domain): enforce non-negative balance on user accounts

Account.debit() raises InsufficientFunds when the resulting balance would
drop below zero. SYSTEM accounts are exempt by design: their negative
balance is the accounting representation of money held outside the service.

PRD: I2, §4.1
```

```
fix(sql): lock participating accounts in deterministic id order

Concurrent A->B and B->A transfers could each hold one row lock and wait
on the other. Ordering the SELECT ... FOR UPDATE by account id removes the
deadlock cycle.

PRD: G2, §5.2
```

```
test(domain): cover ledger balance under parallel transfers

Asserts sum(debits) == sum(credits) and that no USER balance crosses zero
when N threads contend for the same account. Arithmetic-only unit tests
cannot demonstrate this.

PRD: I1, I2, §10
```

```
chore(tooling): guard decision-log hook against dry-run side effects

PRD: n/a — developer workflow, no product change
```
