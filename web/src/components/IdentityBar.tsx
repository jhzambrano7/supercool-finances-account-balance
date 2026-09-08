import { useState } from 'react'
import {
  addIdentity,
  forgetIdentity,
  identityGlyph,
  saveIdentities,
  setActiveIdentity,
  truncateId,
  type Identity,
} from '../identity/identity'

interface Props {
  identities: Identity[]
  activeId: string | null
  onChange: (identities: Identity[], activeId: string | null) => void
}

/**
 * A persistent identity bar, not a login screen (docs/web-ui-plan.md §2).
 * `X-Caller-Id` is the entire authentication mechanism — this bar says so,
 * permanently, rather than dressing a header up as a session.
 */
export function IdentityBar({ identities, activeId, onChange }: Props) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pasteValue, setPasteValue] = useState('')
  const [pasteError, setPasteError] = useState<string | null>(null)

  const active = identities.find((i) => i.id === activeId) ?? null
  const glyph = active ? identityGlyph(active.id) : null

  function switchTo(id: string) {
    setActiveIdentity(id)
    onChange(identities, id)
    setPickerOpen(false)
  }

  function newIdentity() {
    const id = crypto.randomUUID()
    const label = `Person ${identities.length + 1}`
    const next = [...identities, { id, label }]
    saveIdentities(next)
    setActiveIdentity(id)
    onChange(next, id)
  }

  function pasteIdentity() {
    const trimmed = pasteValue.trim()
    const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
    if (!uuidPattern.test(trimmed)) {
      setPasteError('That is not a UUID.')
      return
    }
    const next = addIdentity(identities, trimmed, `Pasted (…${trimmed.slice(-4)})`)
    setActiveIdentity(trimmed)
    onChange(next, trimmed)
    setPasteValue('')
    setPasteError(null)
    setPickerOpen(false)
  }

  function forget(id: string) {
    const next = forgetIdentity(identities, id)
    const nextActive = next[0]?.id ?? null
    if (nextActive) setActiveIdentity(nextActive)
    onChange(next, nextActive)
  }

  return (
    <div className="identity-bar">
      <div className="row">
        {glyph && (
          <div className="identity-swatch" style={{ background: glyph.color }}>
            {glyph.letters}
          </div>
        )}
        <div>
          <div className="mono" title={active?.id} style={{ color: '#fff' }}>
            {active ? `${active.label} · ${truncateId(active.id)}` : 'No identity'}
          </div>
          <div className="identity-bar__notice">
            Simulated identity — sent as <code>X-Caller-Id</code>. There is no authentication.
          </div>
        </div>
      </div>
      <div style={{ position: 'relative' }}>
        <button className="btn-ghost" style={{ color: '#fff', borderColor: 'rgba(255,255,255,0.4)' }} onClick={() => setPickerOpen((v) => !v)}>
          Switch
        </button>
        {pickerOpen && (
          <div
            className="card"
            style={{ position: 'absolute', right: 0, top: '48px', width: '320px', zIndex: 20 }}
          >
            <div className="stack">
              {identities.map((identity) => {
                const g = identityGlyph(identity.id)
                return (
                  <div key={identity.id} className="row" style={{ justifyContent: 'space-between' }}>
                    <button
                      className="btn-ghost"
                      style={{ flex: 1, textAlign: 'left', padding: '8px 12px' }}
                      onClick={() => switchTo(identity.id)}
                    >
                      <span className="identity-swatch" style={{ background: g.color, width: 20, height: 20, fontSize: 9, marginRight: 8 }}>
                        {g.letters}
                      </span>
                      {identity.label}
                      <span className="help-text mono" style={{ marginLeft: 8 }}>
                        {truncateId(identity.id)}
                      </span>
                    </button>
                    {identities.length > 1 && (
                      <button className="btn-ghost" onClick={() => forget(identity.id)} style={{ padding: '4px 10px', fontSize: 12 }}>
                        Forget
                      </button>
                    )}
                  </div>
                )
              })}
              <button className="btn-ghost" onClick={newIdentity}>
                New identity
              </button>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Paste identity</label>
                <div className="row">
                  <input
                    value={pasteValue}
                    onChange={(e) => setPasteValue(e.target.value)}
                    placeholder="owner uuid"
                  />
                  <button className="btn-ghost" onClick={pasteIdentity}>
                    Add
                  </button>
                </div>
                {pasteError && <div className="banner banner--error">{pasteError}</div>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
