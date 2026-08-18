import type { ComponentChildren } from 'preact'
import { SETTING_TARGETS, requestSetting, type SettingTargetId } from './settingTargets'

/**
 * The one way a gated surface offers the switch that ungates it.
 *
 * Every surface that goes inert because something is off says three things, in this order:
 * what is off, what turning it on would do, and a control that goes there. This is the third,
 * and it is one component so that a new gated surface cannot ship a fourth variation of it —
 * the previous arrangement had a `<button class="link">`, a bordered panel button, a plain
 * footer button, and three surfaces with prose naming a switch and no way to reach it at all.
 *
 * It carries no navigation props. The link dispatches `mux:open-setting` and `App` routes it,
 * which is what lets a component nested inside a pane bar or a drawer body offer one without
 * every layer between them having to know about settings navigation.
 */
export function SettingLink({ target, projectId, children, variant = 'button', title }: {
  target: SettingTargetId
  /** Project-scoped targets: which Project. Omitted falls back to the active one. */
  projectId?: string
  children?: ComponentChildren
  /** `link` renders inline inside a sentence; `button` is a standalone control. */
  variant?: 'link' | 'button'
  title?: string
}) {
  const entry = SETTING_TARGETS[target]
  return <button
    type="button"
    class={variant === 'link' ? 'setting-link inline' : 'setting-link'}
    title={title || `${entry.label} · ${entry.where}`}
    onClick={event => { event.stopPropagation(); requestSetting(target, projectId) }}
  >{children || `Turn on ${entry.label}`}</button>
}
