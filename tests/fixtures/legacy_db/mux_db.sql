-- Generated. Do not edit; see tests/support/legacy_database.py.
--
-- `sqlite3 .dump` of a mux.db created by the store code at b45cbe8
-- (b45cbe83b3b209ad63157b38ffa30197137ecfc7) - what an install from that build actually had on disk,
-- rather than a reconstruction of it. Regenerate only to move the
-- baseline forward; the schema versions it recorded then were:
--   {"automation": 3, "prompt_queue": 2, "status_timeline": 1, "telemetry": 3, "tier0": 1}
BEGIN TRANSACTION;
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_type TEXT NOT NULL,
  owner_id TEXT NOT NULL, owner_label TEXT, project_scope_id TEXT NOT NULL,
  relative_path TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  placement_acknowledged_scope_id TEXT,
  UNIQUE(kind,owner_type,owner_id)
);
CREATE TABLE automation_action_results (
  id TEXT PRIMARY KEY, firing_id TEXT NOT NULL, action_index INTEGER NOT NULL,
  kind TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT NOT NULL,
  error TEXT, created_at REAL NOT NULL
);
CREATE TABLE automation_annotations (
  id TEXT PRIMARY KEY, agent_run_id TEXT, project_id TEXT, session_id TEXT,
  tag TEXT NOT NULL, content TEXT NOT NULL, source_event_seq INTEGER,
  evidence_json TEXT, dedupe_key TEXT,
  rule_id TEXT, rule_revision TEXT, provenance TEXT NOT NULL,
  requested_model TEXT, resolved_model TEXT, generation_id TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, confidence REAL, created_at REAL NOT NULL,
  CHECK(agent_run_id IS NOT NULL OR project_id IS NOT NULL)
);
INSERT INTO "automation_annotations" VALUES('legacy-automation_annotations-id','legacy-automation_annotations-agent_run_id','legacy-automation_annotations-project_id','legacy-automation_annotations-session_id','legacy-automation_annotations-tag','legacy-automation_annotations-content',1,'{}','legacy-automation_annotations-dedupe_key','legacy-automation_annotations-rule_id','legacy-automation_annotations-rule_revision','legacy-automation_annotations-provenance','legacy-automation_annotations-requested_model','legacy-automation_annotations-resolved_model','legacy-automation_annotations-generation_id',1,1,1754000000.0,1754000000.0,1754000000.0);
CREATE TABLE automation_budget_ledger (
  id TEXT PRIMARY KEY, day TEXT NOT NULL, rule_id TEXT NOT NULL,
  requested_model TEXT, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL, observer_call_id TEXT, created_at REAL NOT NULL
);
INSERT INTO "automation_budget_ledger" VALUES('legacy-automation_budget_ledger-id','legacy-automation_budget_ledger-day','legacy-automation_budget_ledger-rule_id','legacy-automation_budget_ledger-requested_model',1,1,1754000000.0,'legacy-automation_budget_ledger-observer_call_id',1754000000.0);
CREATE TABLE automation_checkpoints (
  key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE automation_firings (
  id TEXT PRIMARY KEY, event_seq INTEGER NOT NULL, event_type TEXT NOT NULL,
  agent_run_id TEXT, session_id TEXT, rule_id TEXT NOT NULL,
  rule_revision TEXT NOT NULL, chain_id TEXT NOT NULL, chain_depth INTEGER NOT NULL,
  status TEXT NOT NULL, shadow INTEGER NOT NULL DEFAULT 0,
  condition_trace_json TEXT NOT NULL, error TEXT, created_at REAL NOT NULL,
  completed_at REAL, UNIQUE(event_seq,rule_id,rule_revision)
);
INSERT INTO "automation_firings" VALUES('legacy-automation_firings-id',1,'legacy-automation_firings-event_type','legacy-automation_firings-agent_run_id','legacy-automation_firings-session_id','legacy-automation_firings-rule_id','legacy-automation_firings-rule_revision','legacy-automation_firings-chain_id',1,'legacy-automation_firings-status',1,'{}','legacy-automation_firings-error',1754000000.0,1754000000.0);
CREATE TABLE automation_model_cache (
  id INTEGER PRIMARY KEY CHECK(id=1), models_json TEXT NOT NULL,
  fetched_at REAL NOT NULL, error TEXT
);
CREATE TABLE automation_notifications (
  id TEXT PRIMARY KEY, agent_run_id TEXT, session_id TEXT, rule_id TEXT,
  kind TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,
  severity TEXT NOT NULL, evidence_json TEXT NOT NULL,
  read_at REAL, created_at REAL NOT NULL
);
CREATE TABLE automation_observer_calls (
  id TEXT PRIMARY KEY, firing_id TEXT NOT NULL, rule_id TEXT NOT NULL,
  status TEXT NOT NULL, requested_model TEXT, resolved_model TEXT,
  generation_id TEXT, input_hash TEXT NOT NULL, input_bytes INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, latency_ms INTEGER, error TEXT, created_at REAL NOT NULL,
  completed_at REAL
);
INSERT INTO "automation_observer_calls" VALUES('legacy-automation_observer_calls-id','legacy-automation_observer_calls-firing_id','legacy-automation_observer_calls-rule_id','legacy-automation_observer_calls-status','legacy-automation_observer_calls-requested_model','legacy-automation_observer_calls-resolved_model','legacy-automation_observer_calls-generation_id','legacy-automation_observer_calls-input_hash',1,1,1,1754000000.0,1,'legacy-automation_observer_calls-error',1754000000.0,1754000000.0);
CREATE TABLE clipboard_entries (
  id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  text TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  line_count INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  session_id TEXT,
  project_id TEXT,
  device TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
INSERT INTO "clipboard_entries" VALUES('legacy-clipboard_entries-id','legacy-clipboard_entries-content_hash','legacy-clipboard_entries-text',1,1,'legacy-clipboard_entries-source','legacy-clipboard_entries-session_id','legacy-clipboard_entries-project_id','legacy-clipboard_entries-device',1,1754000000.0,1754000000.0);
CREATE TABLE context_compactions (
  id TEXT PRIMARY KEY,
  event_seq INTEGER,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  observed_at REAL NOT NULL,
  source TEXT NOT NULL,
  capability TEXT NOT NULL,
  confidence TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE(session_id,observed_at,source)
);
CREATE TABLE events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session_id TEXT,
  source TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE experience_entries (
  id TEXT PRIMARY KEY, project_scope_id TEXT, backend TEXT,
  error_fingerprint TEXT NOT NULL, error_summary TEXT NOT NULL,
  resolution_summary TEXT NOT NULL, source_run_id TEXT NOT NULL,
  confidence REAL, created_at REAL NOT NULL,
  UNIQUE(error_fingerprint,source_run_id)
);
CREATE TABLE history (
  id TEXT PRIMARY KEY, native_id TEXT NOT NULL, backend TEXT NOT NULL,
  name TEXT NOT NULL, cwd TEXT NOT NULL, project_id TEXT, note_id TEXT,
  spawned_at REAL NOT NULL, exited_at REAL, exit_reason TEXT,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  transcript_path TEXT, external INTEGER NOT NULL DEFAULT 0,
  executable TEXT, argv_json TEXT, pinned_attention INTEGER NOT NULL DEFAULT 0,
  shell_profile_id TEXT, agent_visible INTEGER NOT NULL DEFAULT 0,
  repository_id TEXT, project_label TEXT, project_root TEXT,
  final_state TEXT, context_window INTEGER, final_context_pct REAL,
  peak_context_pct REAL, model TEXT, measurement_source TEXT,
  compaction_count INTEGER NOT NULL DEFAULT 0, last_compaction_at REAL,
  compaction_capability TEXT, compaction_confidence TEXT,
  project_scope_id TEXT, repo_group_id TEXT,
  auto_named INTEGER NOT NULL DEFAULT 1,
  transcript_mtime_ns INTEGER, transcript_size INTEGER,
  native_started_at REAL, last_message_at REAL, last_message_role TEXT,
  time_summary_mtime_ns INTEGER, time_summary_size INTEGER
);
INSERT INTO "history" VALUES('legacy-history-id','legacy-history-native_id','legacy-history-backend','legacy-history-name','legacy-history-cwd','legacy-history-project_id','legacy-history-note_id',1754000000.0,1754000000.0,'legacy-history-exit_reason',1,1,'legacy-history-transcript_path',1,'legacy-history-executable','{}',1,'legacy-history-shell_profile_id',1,'legacy-history-repository_id','legacy-history-project_label','legacy-history-project_root','legacy-history-final_state',1,1754000000.0,1754000000.0,'legacy-history-model','legacy-history-measurement_source',1,1754000000.0,'legacy-history-compaction_capability','legacy-history-compaction_confidence','legacy-history-project_scope_id','legacy-history-repo_group_id',1,1,1,1754000000.0,1754000000.0,'legacy-history-last_message_role',1,1);
CREATE TABLE history_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  history_id TEXT NOT NULL, ordinal INTEGER NOT NULL, role TEXT NOT NULL,
  ts TEXT, text TEXT NOT NULL, source_mtime_ns INTEGER NOT NULL,
  source_size INTEGER NOT NULL, parser_version INTEGER NOT NULL,
  UNIQUE(history_id, ordinal)
);
PRAGMA writable_schema=ON;
INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql)VALUES('table','history_messages_fts','history_messages_fts',0,'CREATE VIRTUAL TABLE history_messages_fts USING fts5(
  text, content=''history_messages'', content_rowid=''id'', tokenize=''unicode61 remove_diacritics 2''
)');
CREATE TABLE 'history_messages_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
INSERT INTO "history_messages_fts_config" VALUES('version',4);
CREATE TABLE 'history_messages_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
INSERT INTO "history_messages_fts_data" VALUES(1,X'');
INSERT INTO "history_messages_fts_data" VALUES(10,X'00000000000000');
CREATE TABLE 'history_messages_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE 'history_messages_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE history_transcript_index (
  history_id TEXT PRIMARY KEY, source_mtime_ns INTEGER NOT NULL,
  source_size INTEGER NOT NULL, parser_version INTEGER NOT NULL,
  message_count INTEGER NOT NULL, indexed_at REAL NOT NULL
);
CREATE TABLE links (
  session_a TEXT NOT NULL, session_b TEXT NOT NULL, mode TEXT NOT NULL,
  created_at REAL NOT NULL, PRIMARY KEY(session_a, session_b)
);
CREATE TABLE observer_batches (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
  selection_json TEXT NOT NULL, preview_json TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0, error TEXT,
  created_at REAL NOT NULL, completed_at REAL
);
CREATE TABLE process_evidence (
  identity_id TEXT PRIMARY KEY,
  pid INTEGER NOT NULL,
  creation_time REAL NOT NULL,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  executable TEXT,
  command_hash TEXT NOT NULL,
  parent_pid INTEGER,
  parent_lineage_json TEXT NOT NULL DEFAULT '[]',
  job_assignment TEXT NOT NULL,
  state TEXT NOT NULL,
  reason TEXT NOT NULL,
  confidence TEXT NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  last_verified_at REAL,
  exited_at REAL,
  exit_evidence TEXT,
  inaccessible_count INTEGER NOT NULL DEFAULT 0,
  startup_revalidated INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "process_evidence" VALUES('legacy-process_evidence-identity_id',1,1754000000.0,'legacy-process_evidence-session_id','legacy-process_evidence-agent_run_id','legacy-process_evidence-project_id','legacy-process_evidence-executable','legacy-process_evidence-command_hash',1,'{}','legacy-process_evidence-job_assignment','legacy-process_evidence-state','legacy-process_evidence-reason','legacy-process_evidence-confidence',1754000000.0,1754000000.0,1754000000.0,1754000000.0,'legacy-process_evidence-exit_evidence',1,1);
CREATE TABLE project_cards (
  project_id TEXT PRIMARY KEY, project_root TEXT NOT NULL,
  fingerprint TEXT NOT NULL, card_json TEXT NOT NULL,
  schema_version INTEGER NOT NULL, requested_model TEXT, resolved_model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL, created_at REAL NOT NULL
);
CREATE TABLE project_groups (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL
);
CREATE TABLE project_scopes (
  id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
  source TEXT NOT NULL, repo_group_id TEXT, hidden INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, last_activity REAL NOT NULL
);
CREATE TABLE projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE,
  position INTEGER NOT NULL, group_id TEXT, layout_json TEXT,
  default_backend TEXT, layout_revision INTEGER NOT NULL DEFAULT 0,
  default_profile_id TEXT, resource_open_mode TEXT,
  sidebar_visible INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE queue_auto_counters (
  name TEXT PRIMARY KEY,
  value REAL NOT NULL DEFAULT 0,
  updated_at REAL
);
CREATE TABLE queue_auto_policy (
  session_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  agent_run_id TEXT,
  accept_agent_messages INTEGER NOT NULL DEFAULT 0,
  expires_at REAL,
  max_sends INTEGER NOT NULL DEFAULT 0,
  sends_used INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,
  disabled_reason TEXT,
  enabled_at REAL,
  updated_at REAL NOT NULL,
  updated_by TEXT
);
CREATE TABLE queue_deliveries (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  idempotency_key TEXT,
  revision INTEGER NOT NULL,
  target_session_id TEXT NOT NULL,
  target_agent_run_id TEXT,
  delivery_state TEXT,
  reasons_json TEXT,
  confirmed INTEGER NOT NULL DEFAULT 0,
  initiator TEXT NOT NULL DEFAULT 'user',
  outcome TEXT NOT NULL,
  error TEXT,
  bytes INTEGER,
  created_at REAL NOT NULL,
  completed_at REAL
);
CREATE TABLE queue_messages (
  id TEXT PRIMARY KEY,
  target_session_id TEXT NOT NULL,
  target_agent_run_id TEXT,
  target_backend TEXT,
  target_label TEXT,
  project_id TEXT,
  position INTEGER NOT NULL,
  state TEXT NOT NULL,
  body TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  sender_kind TEXT NOT NULL DEFAULT 'user',
  sender_id TEXT,
  sender_label TEXT,
  origin_session_id TEXT,
  correlation_id TEXT,
  chain_depth INTEGER NOT NULL DEFAULT 0,
  origin_json TEXT,
  payload_json TEXT,
  constraints_json TEXT,
  blocked_reasons_json TEXT,
  stranded_reason TEXT,
  cancel_kind TEXT,
  retargeted_from_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  edited_at REAL,
  armed_at REAL,
  sent_at REAL
);
INSERT INTO "queue_messages" VALUES('legacy-queue_messages-id','legacy-queue_messages-target_session_id','legacy-queue_messages-target_agent_run_id','legacy-queue_messages-target_backend','legacy-queue_messages-target_label','legacy-queue_messages-project_id',1,'legacy-queue_messages-state','legacy-queue_messages-body',1,'legacy-queue_messages-sender_kind','legacy-queue_messages-sender_id','legacy-queue_messages-sender_label','legacy-queue_messages-origin_session_id','legacy-queue_messages-correlation_id',1,'{}','{}','{}','{}','legacy-queue_messages-stranded_reason','legacy-queue_messages-cancel_kind','{}',1754000000.0,1754000000.0,1754000000.0,1754000000.0,1754000000.0);
CREATE TABLE quota_attributions (
  sample_id INTEGER NOT NULL,
  window TEXT NOT NULL,
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  interval_start REAL NOT NULL,
  interval_end REAL NOT NULL,
  quota_delta REAL NOT NULL,
  correlated_estimate REAL NOT NULL,
  correlated_low REAL NOT NULL,
  correlated_high REAL NOT NULL,
  external_estimate REAL NOT NULL,
  external_low REAL NOT NULL,
  external_high REAL NOT NULL,
  confidence TEXT NOT NULL,
  sample_gap_seconds REAL NOT NULL,
  concurrent_sessions INTEGER NOT NULL,
  provider_lag_seconds REAL NOT NULL,
  allocations_json TEXT NOT NULL,
  caveats_json TEXT NOT NULL,
  PRIMARY KEY(sample_id,window)
);
CREATE TABLE quota_reset_events (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  window TEXT NOT NULL,
  before_sample_id INTEGER NOT NULL,
  after_sample_id INTEGER NOT NULL,
  confirmation_sample_id INTEGER,
  before_value REAL NOT NULL,
  after_value REAL NOT NULL,
  expected_reset_at REAL,
  observed_at REAL NOT NULL,
  classification TEXT NOT NULL,
  confidence TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0,
  suppression_reason TEXT,
  created_at REAL NOT NULL,
  confirmed_at REAL,
  review_status TEXT,
  reviewed_at REAL
);
INSERT INTO "quota_reset_events" VALUES('legacy-quota_reset_events-id','legacy-quota_reset_events-provider','legacy-quota_reset_events-account_id','legacy-quota_reset_events-window',1,1,1,1754000000.0,1754000000.0,1754000000.0,1754000000.0,'legacy-quota_reset_events-classification','legacy-quota_reset_events-confidence',1,'legacy-quota_reset_events-suppression_reason',1754000000.0,1754000000.0,'legacy-quota_reset_events-review_status',1754000000.0);
CREATE TABLE quota_sample_rollups (
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  day TEXT NOT NULL,
  samples INTEGER NOT NULL,
  errors INTEGER NOT NULL,
  session_min REAL,
  session_max REAL,
  session_first REAL,
  session_last REAL,
  weekly_min REAL,
  weekly_max REAL,
  weekly_first REAL,
  weekly_last REAL,
  PRIMARY KEY(provider,account_id,day)
);
CREATE TABLE quota_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  sampled_at REAL NOT NULL,
  status TEXT NOT NULL,
  session_used REAL,
  weekly_used REAL,
  session_reset_at REAL,
  weekly_reset_at REAL,
  fable_used REAL,
  fable_reset_at REAL,
  source TEXT,
  freshness TEXT NOT NULL,
  raw_precision INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  account_active INTEGER NOT NULL,
  auth_state TEXT NOT NULL, provider_account_uuid TEXT,
  UNIQUE(provider,account_id,sampled_at)
);
INSERT INTO "quota_samples" VALUES(1,'legacy-quota_samples-provider','legacy-quota_samples-account_id',1754000000.0,'legacy-quota_samples-status',1754000000.0,1754000000.0,1754000000.0,1754000000.0,1754000000.0,1754000000.0,'legacy-quota_samples-source','legacy-quota_samples-freshness',1,'legacy-quota_samples-error',1,'legacy-quota_samples-auth_state','legacy-quota_samples-provider_account_uuid');
CREATE TABLE repo_groups (
  id TEXT PRIMARY KEY, label TEXT NOT NULL, source TEXT NOT NULL,
  created_at REAL NOT NULL, last_activity REAL NOT NULL
);
CREATE TABLE schema_versions(store TEXT PRIMARY KEY, version INTEGER NOT NULL);
INSERT INTO "schema_versions" VALUES('telemetry',3);
INSERT INTO "schema_versions" VALUES('tier0',1);
INSERT INTO "schema_versions" VALUES('status_timeline',1);
INSERT INTO "schema_versions" VALUES('automation',3);
INSERT INTO "schema_versions" VALUES('prompt_queue',2);
CREATE TABLE session_lineage (
  id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, child_run_id TEXT NOT NULL,
  relation TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(parent_run_id,child_run_id,relation)
);
CREATE TABLE status_timeline (
  session_id TEXT NOT NULL,
  agent_run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  entry_json TEXT NOT NULL,
  PRIMARY KEY(session_id, agent_run_id, seq)
);
CREATE TABLE tier0_facts (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  kind TEXT NOT NULL,
  target TEXT,
  content_hash TEXT,
  fingerprint TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  source_seq INTEGER,
  source_ref TEXT,
  created_at REAL NOT NULL
);
INSERT INTO "tier0_facts" VALUES('legacy-tier0_facts-id','legacy-tier0_facts-session_id','legacy-tier0_facts-agent_run_id','legacy-tier0_facts-project_id','legacy-tier0_facts-kind','legacy-tier0_facts-target','legacy-tier0_facts-content_hash','legacy-tier0_facts-fingerprint','{}',1,'legacy-tier0_facts-source_ref',1754000000.0);
CREATE TABLE tool_events (
  id TEXT PRIMARY KEY,
  event_seq INTEGER,
  source_identity TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  agent_run_id TEXT,
  project_id TEXT,
  backend TEXT NOT NULL,
  model TEXT,
  observed_at REAL NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  raw_tool TEXT NOT NULL,
  taxonomy TEXT NOT NULL,
  success INTEGER,
  exit_code INTEGER,
  duration_ms REAL,
  parser_version TEXT NOT NULL,
  explicit_skill TEXT
);
CREATE TABLE transcript_telemetry_coverage (
  session_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  project_id TEXT,
  transcript_path_hash TEXT,
  transcript_mtime_ns INTEGER,
  transcript_size INTEGER,
  parser_version TEXT NOT NULL,
  status TEXT NOT NULL,
  recognized_records INTEGER NOT NULL,
  unknown_records INTEGER NOT NULL,
  tool_events INTEGER NOT NULL,
  skill_events INTEGER NOT NULL,
  compaction_events INTEGER NOT NULL,
  reconciled_at REAL NOT NULL,
  diagnostic TEXT
);
CREATE TABLE voice_clips (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_run_id TEXT,
    created_at REAL NOT NULL,
    trigger TEXT NOT NULL,
    content_mode TEXT NOT NULL,
    engine TEXT NOT NULL,
    voice TEXT NOT NULL,
    text TEXT NOT NULL,
    file_path TEXT NOT NULL,
    format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    duration_hint_s REAL,
    status TEXT NOT NULL,
    error TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
);
INSERT INTO "voice_clips" VALUES('legacy-voice_clips-id','legacy-voice_clips-session_id','legacy-voice_clips-agent_run_id',1754000000.0,'legacy-voice_clips-trigger','legacy-voice_clips-content_mode','legacy-voice_clips-engine','legacy-voice_clips-voice','legacy-voice_clips-text','legacy-voice_clips-file_path','legacy-voice_clips-format',1,1754000000.0,'legacy-voice_clips-status','legacy-voice_clips-error','legacy-voice_clips-model',1,1,1754000000.0);
CREATE TRIGGER history_messages_ai AFTER INSERT ON history_messages BEGIN
  INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE TRIGGER history_messages_ad AFTER DELETE ON history_messages BEGIN
  INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
  VALUES('delete',old.id,old.text);
END;
CREATE TRIGGER history_messages_au AFTER UPDATE ON history_messages BEGIN
  INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
  VALUES('delete',old.id,old.text);
  INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE INDEX idx_events_ts ON events(ts);
CREATE INDEX idx_events_session ON events(session_id, ts);
CREATE INDEX idx_history_spawned ON history(spawned_at DESC);
CREATE INDEX idx_history_messages_history ON history_messages(history_id, ordinal);
CREATE INDEX idx_history_messages_source
  ON history_messages(history_id, source_mtime_ns, source_size, parser_version);
CREATE INDEX idx_history_agent_project ON history(agent_visible,project_id,spawned_at DESC);
CREATE INDEX idx_history_agent_filters ON history(agent_visible,backend,project_id,external,spawned_at DESC);
CREATE INDEX idx_history_project ON history(project_id);
CREATE INDEX idx_history_scope ON history(project_scope_id,spawned_at DESC);
CREATE INDEX idx_artifacts_scope ON artifacts(project_scope_id);
CREATE INDEX idx_process_evidence_owner
  ON process_evidence(session_id,state,last_seen DESC);
CREATE INDEX idx_process_evidence_retention
  ON process_evidence(last_seen,state);
CREATE INDEX idx_quota_samples_account
  ON quota_samples(provider,account_id,sampled_at DESC);
CREATE INDEX idx_quota_resets_recent
  ON quota_reset_events(observed_at DESC);
CREATE INDEX idx_quota_resets_pending
  ON quota_reset_events(provider,account_id,window,confirmed,observed_at DESC);
CREATE INDEX idx_quota_attribution_recent
  ON quota_attributions(interval_end DESC);
CREATE INDEX idx_context_compactions_session
  ON context_compactions(session_id,observed_at DESC);
CREATE INDEX idx_tool_events_metrics
  ON tool_events(observed_at,backend,project_id,taxonomy);
CREATE INDEX idx_tier0_session ON tier0_facts(session_id,created_at DESC);
CREATE INDEX idx_tier0_kind ON tier0_facts(kind,created_at DESC);
CREATE INDEX idx_tier0_hash ON tier0_facts(content_hash);
CREATE INDEX idx_tier0_fingerprint ON tier0_facts(session_id,fingerprint);
CREATE INDEX idx_tier0_retention ON tier0_facts(created_at);
CREATE INDEX idx_tier0_run ON tier0_facts(agent_run_id,created_at);
CREATE INDEX idx_tier0_project ON tier0_facts(project_id,created_at);
CREATE INDEX idx_status_timeline_session_ts
  ON status_timeline(session_id, ts);
CREATE INDEX idx_status_timeline_run_ts
  ON status_timeline(agent_run_id, ts);
CREATE INDEX idx_status_timeline_ts ON status_timeline(ts);
CREATE INDEX idx_annotations_run
  ON automation_annotations(agent_run_id,created_at DESC);
CREATE INDEX idx_annotations_project
  ON automation_annotations(project_id,created_at DESC);
CREATE INDEX idx_annotations_tag
  ON automation_annotations(tag,created_at DESC);
CREATE UNIQUE INDEX idx_annotations_dedupe
  ON automation_annotations(dedupe_key);
CREATE INDEX idx_firings_rule
  ON automation_firings(rule_id,created_at DESC);
CREATE INDEX idx_budget_day_rule
  ON automation_budget_ledger(day,rule_id);
CREATE INDEX idx_notifications_unread
  ON automation_notifications(read_at,created_at DESC);
CREATE INDEX idx_experience_fingerprint
  ON experience_entries(error_fingerprint,created_at DESC);
CREATE INDEX idx_action_firing
  ON automation_action_results(firing_id);
CREATE INDEX idx_observer_firing
  ON automation_observer_calls(firing_id);
CREATE INDEX idx_observer_created
  ON automation_observer_calls(created_at);
CREATE INDEX idx_lineage_child
  ON session_lineage(child_run_id);
CREATE INDEX idx_voice_clips_session ON voice_clips(session_id, created_at);
CREATE INDEX idx_voice_clips_run ON voice_clips(agent_run_id, created_at);
CREATE INDEX idx_queue_messages_target
  ON queue_messages(target_session_id, position);
CREATE INDEX idx_queue_messages_state
  ON queue_messages(state, updated_at DESC);
CREATE INDEX idx_queue_messages_sender
  ON queue_messages(sender_kind, sender_id, created_at DESC);
CREATE UNIQUE INDEX idx_queue_messages_correlation
  ON queue_messages(sender_kind, sender_id, correlation_id)
  WHERE correlation_id IS NOT NULL;
CREATE UNIQUE INDEX idx_queue_deliveries_idempotency
  ON queue_deliveries(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_queue_deliveries_message
  ON queue_deliveries(message_id, created_at DESC);
CREATE UNIQUE INDEX idx_clipboard_hash ON clipboard_entries(content_hash);
CREATE INDEX idx_clipboard_recent ON clipboard_entries(updated_at DESC);
PRAGMA writable_schema=OFF;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('quota_samples',1);
COMMIT;
