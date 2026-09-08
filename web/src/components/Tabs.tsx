interface Tab {
  id: string
  label: string
}

interface Props {
  tabs: Tab[]
  activeId: string
  onChange: (id: string) => void
}

/** A hand-written tab switch — no router beyond this (docs/web-ui-plan.md §8). */
export function Tabs({ tabs, activeId, onChange }: Props) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === activeId}
          className="tab"
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
