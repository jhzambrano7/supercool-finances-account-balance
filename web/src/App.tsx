import { useState } from 'react'
import { IdentityBar } from './components/IdentityBar'
import { PlaceholderScreen } from './components/PlaceholderScreen'
import { Tabs } from './components/Tabs'
import { loadIdentities, type Identity } from './identity/identity'
import { AccountsScreen } from './screens/AccountsScreen'
import { MovementHistoryScreen } from './screens/MovementHistoryScreen'
import { MoveMoneyScreen } from './screens/MoveMoneyScreen'
import { OpenAccountScreen } from './screens/OpenAccountScreen'

type Screen = 'open' | 'move' | 'accounts' | 'history' | 'reversal'

const SCREENS: { id: Screen; label: string }[] = [
  { id: 'open', label: 'Open account' },
  { id: 'move', label: 'Move money' },
  { id: 'accounts', label: 'Accounts' },
  { id: 'history', label: 'Movement history' },
  { id: 'reversal', label: 'Reversal' },
]

export default function App() {
  const [{ identities, activeId }, setIdentityState] = useState(() => loadIdentities())
  const [screen, setScreen] = useState<Screen>('open')

  const activeId_ = activeId
  const active: Identity | undefined = identities.find((i) => i.id === activeId_)

  if (!active) {
    return (
      <div className="app-shell">
        <div className="banner banner--error">No identity available. Reload the page.</div>
      </div>
    )
  }

  return (
    <div>
      <IdentityBar
        identities={identities}
        activeId={activeId}
        onChange={(nextIdentities, nextActiveId) => setIdentityState({ identities: nextIdentities, activeId: nextActiveId })}
      />
      <div className="app-shell">
        <Tabs tabs={SCREENS} activeId={screen} onChange={(id) => setScreen(id as Screen)} />

        {screen === 'open' && <OpenAccountScreen ownerId={active.id} />}
        {screen === 'move' && <MoveMoneyScreen ownerId={active.id} onSwitchIdentity={() => setScreen('open')} />}
        {screen === 'accounts' && <AccountsScreen ownerId={active.id} />}
        {screen === 'history' && <MovementHistoryScreen ownerId={active.id} />}
        {screen === 'reversal' && (
          <PlaceholderScreen
            title="Reversal"
            reason="Not on this branch, and reversal is operator-authorized, not customer-initiated — it belongs behind an operator identity this UI does not have yet."
            specLink="openspec/specs/revert/spec.md"
          />
        )}
      </div>
    </div>
  )
}
