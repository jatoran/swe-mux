import type { ComponentChildren } from 'preact'
import { displayModelName } from './modelDisplay'

export function ModelName({
  model,
  fallback = 'model unavailable',
}: {
  model?: string | null
  fallback?: ComponentChildren
}) {
  if (!model) return <>{fallback}</>
  const display = displayModelName(model)
  return <span title={display === model ? undefined : model} aria-label={model}>{display}</span>
}
