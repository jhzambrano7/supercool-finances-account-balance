/**
 * Identity, in a UI with no login (docs/web-ui-plan.md §2).
 *
 * `X-Caller-Id` is a raw owner UUID in a header -- there is no token, no
 * session, nothing to log out of. This module is a `localStorage`-backed
 * list of simulated identities, never a real auth client.
 */

export interface Identity {
  id: string
  label: string
}

const IDENTITIES_KEY = 'scf.identities'
const ACTIVE_KEY = 'scf.activeIdentityId'

function readIdentities(): Identity[] {
  try {
    const raw = localStorage.getItem(IDENTITIES_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeIdentities(identities: Identity[]): void {
  try {
    localStorage.setItem(IDENTITIES_KEY, JSON.stringify(identities))
  } catch {
    // best-effort; a private/blocked-storage browser just loses persistence
  }
}

function readActiveId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY)
  } catch {
    return null
  }
}

function writeActiveId(id: string): void {
  try {
    localStorage.setItem(ACTIVE_KEY, id)
  } catch {
    // best-effort
  }
}

/** Seeds two identities on first run -- every interesting flow here needs two people. */
export function loadIdentities(): { identities: Identity[]; activeId: string | null } {
  let identities = readIdentities()
  if (identities.length === 0) {
    identities = [
      { id: crypto.randomUUID(), label: 'Person 1' },
      { id: crypto.randomUUID(), label: 'Person 2' },
    ]
    writeIdentities(identities)
  }
  let activeId = readActiveId()
  if (!activeId || !identities.some((i) => i.id === activeId)) {
    activeId = identities[0]?.id ?? null
    if (activeId) writeActiveId(activeId)
  }
  return { identities, activeId }
}

export function saveIdentities(identities: Identity[]): void {
  writeIdentities(identities)
}

export function setActiveIdentity(id: string): void {
  writeActiveId(id)
}

export function addIdentity(identities: Identity[], id: string, label: string): Identity[] {
  if (identities.some((i) => i.id === id)) return identities
  const next = [...identities, { id, label }]
  writeIdentities(next)
  return next
}

export function forgetIdentity(identities: Identity[], id: string): Identity[] {
  const next = identities.filter((i) => i.id !== id)
  writeIdentities(next)
  return next
}

export function truncateId(id: string): string {
  if (id.length <= 13) return id
  return `${id.slice(0, 8)}…${id.slice(-4)}`
}

/** Deterministic colour + two-letter glyph so two identities are distinguishable at a glance. */
export function identityGlyph(id: string): { color: string; letters: string } {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0
  }
  const hue = Math.abs(hash) % 360
  const letters = id.replace(/-/g, '').slice(0, 2).toUpperCase()
  return { color: `hsl(${hue}, 55%, 45%)`, letters }
}
