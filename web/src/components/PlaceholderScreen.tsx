interface Props {
  title: string
  reason: string
  specLink: string
}

/** Blocked features say so honestly, and link to why, rather than simulating anything (§5.6, §5.7). */
export function PlaceholderScreen({ title, reason, specLink }: Props) {
  return (
    <div className="stack">
      <h2 className="screen-title">{title}</h2>
      <div className="banner banner--info">
        {reason} See <span className="mono">{specLink}</span>.
      </div>
    </div>
  )
}
