import type { DescribedError } from '../api/errors'

interface Props {
  error: DescribedError
  onRetry?: () => void
  onSwitchIdentity?: () => void
  onStartOver?: () => void
}

/** Renders a mapped error as a human story, never a bare status code (docs/web-ui-plan.md §7). */
export function ErrorBanner({ error, onRetry, onSwitchIdentity, onStartOver }: Props) {
  return (
    <div className="banner banner--error stack" role="alert">
      <div>
        <strong>{error.title}</strong>
        {error.detail && <div>{error.detail}</div>}
      </div>
      <div className="row">
        {error.recovery === 'retry' && onRetry && (
          <button className="btn-ghost" onClick={onRetry}>
            Retry
          </button>
        )}
        {error.recovery === 'try-again' && onRetry && (
          <button className="btn-ghost" onClick={onRetry}>
            Try again
          </button>
        )}
        {error.recovery === 'switch-identity' && onSwitchIdentity && (
          <button className="btn-ghost" onClick={onSwitchIdentity}>
            Switch identity
          </button>
        )}
        {error.recovery === 'start-over' && onStartOver && (
          <button className="btn-ghost" onClick={onStartOver}>
            Start over
          </button>
        )}
      </div>
    </div>
  )
}
