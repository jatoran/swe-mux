export type SessionState = 'starting' | 'running' | 'working' | 'idle' | 'awaiting' | 'exited' | 'crashed'

export interface Session {
  id: string; name: string; space_id: string; backend: 'shell' | 'claude' | 'codex'
  native_session_id: string; cwd: string; exe: string; args: string[]; pid: number
  created_at: number; state: SessionState; state_detail?: string; tokens_in: number
  tokens_out: number; context_window: number; context_pct: number; last_activity_ts: number
  git: { branch?: string; dirty: number; ahead: number; behind: number }
  pinned_attention: boolean; broadcast: boolean
}

export interface Space { id: string; name: string; position: number; layout: unknown }
