import type { PaneLayout } from './layout'

export type SessionState = 'starting' | 'running' | 'working' | 'idle' | 'awaiting' | 'exited' | 'crashed'

export interface Session {
  id: string; name: string; space_id: string; backend: 'shell' | 'claude' | 'codex'
  native_session_id: string; cwd: string; exe: string; args: string[]; pid: number
  created_at: number; state: SessionState; state_detail?: string; tokens_in: number
  tokens_out: number; context_window: number; context_pct: number; last_activity_ts: number
  git: { branch?: string; dirty: number; ahead: number; behind: number }
  pinned_attention: boolean; broadcast: boolean
  shell_profile_id?: string
  context_peak_pct:number;model?:string;measurement_source?:string
  project_id?:string;project_label?:string;project_root?:string
  project_scope_id?:string;repo_group_id?:string
  spawn_cwd?:string;spawn_project_scope_id?:string;spawn_repo_group_id?:string;spawn_project_label?:string;spawn_project_root?:string
  runtime_cwd?:string;runtime_cwd_live:boolean;runtime_cwd_source:string;runtime_cwd_updated_at?:number
  runtime_project_scope_id?:string;runtime_cwd_dropped:number;agent_run_id?:string;agent_run_started_at?:number
  run_cwd?:string;run_project_scope_id?:string;run_repo_group_id?:string
  parser_status?:string;parser_diagnostic?:string;parser_events_seen?:number
}

export interface Space { id: string; name: string; position: number; layout: PaneLayout | unknown; layout_revision:number; default_cwd?:string; default_backend?:string; default_profile_id?:string }

export interface ProjectScope {
  id:string;root:string;label:string;source:string;repo_group_id?:string;repo_group_label?:string
  hidden:number;created_at:number;last_activity:number;root_exists:boolean;live_count:number
  history_count:number;artifact_count:number
  inventory?:{root_exists:boolean;config_exists:boolean;rules_present_inert:boolean;unlinked:Array<{path:string;kind:string;filename:string}>;conflicting?:Array<{path:string;kind:string;filename:string;other_project_scope_ids:string[]}>;scan_truncated:boolean}
  artifacts?:Array<{id:string;kind:string;owner_type:string;owner_id:string;owner_label?:string;project_scope_id:string;relative_path:string;revision?:string;placement_acknowledged_scope_id?:string}>
  blockers?:{history:number;artifacts:number}
  detached_artifacts?:Array<{id:string;kind:string;owner_type:string;owner_id:string;owner_label?:string;relative_path:string}>
}

export interface ShellProfile {
  id:string; label:string; executable:string; args:string[]; env:Record<string,string>
  platforms:string[]; cwd_strategy:'native'|'home'|'wsl'; marker:string
  capabilities:string[]; cwd_integration:boolean; enabled:boolean; configured?:boolean
}
