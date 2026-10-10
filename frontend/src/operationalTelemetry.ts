// Shapes for `/api/telemetry/operational`, which two dialogs now read for different halves.
//
// Usage -> Quota reads `quota.usage_intervals`; Resources -> Fleet activity reads `tools` and
// `compactions`. The payload used to be fetched once because all of it lived in one segment
// of one dialog. It no longer does, and threading it between two modals would couple them
// for nothing: each is unmounted when its segment is not selected, and the endpoint is a
// bounded read of a local SQLite store rather than a poll, so two reads cost two queries.
//
// The types live here rather than beside either reader because neither owns them. A field
// added to the tools payload has to reach the fleet view and a field added to the quota
// payload has to reach the usage view, and a copy in each file is how those drift.

import type { QuotaUsageInterval } from './usageAnalytics'

export type ToolMetric = {
  backend: string
  model: string
  project_id: string
  session_id: string
  taxonomy: string
  raw_tool: string
  events: number
  uses: number
  errors: number
  average_duration_ms?: number | null
}

export type SkillMetric = {
  explicit_skill: string
  backend: string
  project_id: string
  uses: number
  last_used_at: number
}

/** Per-run reconciliation state for the transcript parsers. Diagnostic, not a metric: it
 *  says whether the numbers beside it were collectable, which is a different claim from
 *  what they are. */
export type ParserCoverage = {
  session_id: string
  backend: string
  parser_version: string
  status: string
  recognized_records: number
  unknown_records: number
  tool_events: number
  skill_events: number
  compaction_events: number
  reconciled_at: number
  diagnostic?: string
}

export type CompactionRecord = {
  session_id: string
  backend: string
  project_id: string
  count: number
  last_compaction_at: number
  capability: string
  confidence: string
}

export type OperationalStatus = {
  schema_version: number
  interpretation: string
  quota: {
    samples: unknown[]
    resets: unknown[]
    /** Intervals that rose, newest first (`quota_usage.py`). */
    usage_intervals: QuotaUsageInterval[]
    rollups: unknown[]
  }
  tools: {
    metrics: ToolMetric[]
    skills: SkillMetric[]
    unknown_or_unmapped: number
    parser_version: string
    parser_versions: Record<string, string>
    coverage: ParserCoverage[]
  }
  compactions: CompactionRecord[]
}

/** The bounded read both dialogs issue. The limit is a row cap on the durable store, not a
 *  window: an unbounded read of an append-only evidence table grows without end. */
export const OPERATIONAL_TELEMETRY_PATH = '/api/telemetry/operational?limit=300'
