// Every displayed telemetry total names four things: its time range, its cohort, its
// denominator, and how the window was answered (rolled-up days and hours against raw
// entity reads). A figure drawn without them is the bug this component exists to stop:
// "4,821 calls" means nothing until the reader knows over which days, for whose runs,
// out of what, and whether a rebuild is still pending. The words live in
// `telemetryCaptionText.ts` so a unit test can hold them to that.

import { type Coverage, captionText } from './telemetryCaptionText'

export type { Coverage }
export { cohortLabel, coverageLabel, rangeLabel } from './telemetryCaptionText'

export function TelemetryCaption(props: {
  days: number; origin: string; denominator: string; coverage?: Coverage; filters?: Record<string, string>
}) {
  return <small class="telemetry-total-caption" data-telemetry-caption>{captionText(props)}</small>
}
