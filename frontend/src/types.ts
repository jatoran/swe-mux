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
  parser_status?:string;parser_diagnostic?:string;parser_events_seen?:number
}

export interface Space { id: string; name: string; position: number; layout: PaneLayout | unknown; layout_revision:number; default_cwd?:string; default_backend?:string; default_profile_id?:string }

export interface ShellProfile {
  id:string; label:string; executable:string; args:string[]; env:Record<string,string>
  platforms:string[]; cwd_strategy:'native'|'home'|'wsl'; marker:string
  capabilities:string[]; enabled:boolean; configured?:boolean
}
