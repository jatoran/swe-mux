import { rich, t } from './localization/index.ts'
/**
 * The one control every spending cap is edited with.
 *
 * A budget is `{tokens?, usd?, mode}` (`src/swe_mux/budget.py`), and the mode is
 * the part this control exists to make legible: which axis actually stops the
 * feature. Before it, the automation ceilings rendered as four unrelated number
 * boxes whose relationship - both enforced, whichever is hit first - was
 * knowable only from the enforcement code, and every dollar-only cap presented
 * as a lone figure with no hint that a token cap was even possible.
 *
 * Three rules the layout follows:
 *
 * - **The mode is chosen first and drawn first**, because it decides whether the
 *   fields below it mean anything.
 * - **A field the mode does not enforce is still editable and clearly marked
 *   unenforced.** Disabling it would throw the operator's number away every time
 *   they looked at the other axis; hiding the state would be worse, because an
 *   unenforced figure reads exactly like a cap.
 * - **A provider that reports no cost is stated here**, at the moment the dollar
 *   axis is being chosen. A dollar cap cannot bind against a local llama.cpp or
 *   Ollama endpoint - absent cost is unknown, never zero - so the control says
 *   so and names first-hit as the configuration that still bounds it.
 */
import type { Budget, BudgetMode } from './types'

/** Every mode, with the sentence that says what it does rather than its name. */
const MODES: { value: BudgetMode; label: string; hint: string }[] = [
  { value: 'either', get label() { return t("BudgetControl.9780905021") }, get hint() { return t("BudgetControl.432f375253") } },
  { value: 'tokens', get label() { return t("BudgetControl.a039dfb962") }, get hint() { return t("BudgetControl.ad109a1e6b") } },
  { value: 'usd', get label() { return t("BudgetControl.4227cb0785") }, get hint() { return t("BudgetControl.70b00e6a1c") } },
]

export const costBlindWarning =
  () => (t("BudgetControl.babe975122") +
  t("BudgetControl.9e10454d5a") +
  t("BudgetControl.d70a2697d7"))

type Props = {
  /** Config key. Also the `data-setting` mark a deep link scrolls to. */
  name: string
  label: string
  value: Budget
  onChange: (next: Budget) => void
  /** Bounds mirroring the daemon's, so a rejected save is a rare surprise. */
  maxTokens?: number
  maxUsd?: number
  minTokens?: number
  usdStep?: number
  /**
   * Whether the configured provider reports completion cost. `false` draws the
   * warning above; leaving it undefined draws nothing, because "we have not
   * asked yet" must not render as an accusation against a working endpoint.
   */
  reportsCost?: boolean
  /**
   * Calls in the current window whose cost the provider never reported. Observed
   * evidence rather than a capability, so it is shown even on a provider that
   * usually prices - a run of unpriced calls means the dollar figure beside the
   * cap is a floor.
   */
  unpricedCalls?: number
  hint?: string
}

function enforces(mode: BudgetMode, axis: 'tokens' | 'usd'): boolean {
  return mode === 'either' || mode === axis
}

export function BudgetControl({
  name, label, value, onChange,
  maxTokens = 100_000_000, maxUsd = 10_000, minTokens = 0, usdStep = 0.01,
  reportsCost, unpricedCalls = 0, hint,
}: Props) {
  const mode = value.mode ?? 'either'
  const tokensOn = enforces(mode, 'tokens')
  const usdOn = enforces(mode, 'usd')
  // Switching *into* a mode with no figure for the axis it now enforces would
  // save a budget the daemon rejects, so the empty axis is seeded from the
  // control's own ceiling - a limit the operator can see and lower beats a
  // validation error naming a field that was never on screen.
  const setMode = (next: BudgetMode) => onChange({
    tokens: enforces(next, 'tokens') && value.tokens == null ? maxTokens : value.tokens ?? null,
    usd: enforces(next, 'usd') && value.usd == null ? maxUsd : value.usd ?? null,
    mode: next,
  })
  const setTokens = (raw: string) => onChange({ ...value, tokens: raw === '' ? null : Number(raw) })
  const setUsd = (raw: string) => onChange({ ...value, usd: raw === '' ? null : Number(raw) })

  return (
    <fieldset class="budget-control" data-setting={name}>
      <legend>{label}</legend>
      {hint && <p class="budget-hint">{hint}</p>}
      <div class="budget-modes" role="radiogroup" aria-label={t("BudgetControl.76b8d1d3ed", { label: label })}>
        {MODES.map(option => (
          <label key={option.value} class="budget-mode" title={option.hint}>
            <input
              type="radio"
              name={`${name}__mode`}
              value={option.value}
              checked={mode === option.value}
              onChange={() => setMode(option.value)}
            />
            {option.label}
          </label>
        ))}
      </div>
      <p class="budget-mode-hint">{MODES.find(item => item.value === mode)?.hint}</p>
      <div class="budget-axes">
        <label class={tokensOn ? 'budget-axis' : 'budget-axis budget-axis-off'}>{rich("BudgetControl.5761a50cfd", { value1: <input
            type="number"
            min={minTokens}
            max={maxTokens}
            value={value.tokens ?? ''}
            aria-label={t("BudgetControl.2d1be05d7f", { label: label })}
            onInput={event => setTokens(event.currentTarget.value)}
          />, value2: !tokensOn && <small>{t("BudgetControl.a2916ea458")}</small> })}</label>
        <label class={usdOn ? 'budget-axis' : 'budget-axis budget-axis-off'}>{rich("BudgetControl.dc2d9347ac", { value1: <input
            type="number"
            min="0"
            max={maxUsd}
            step={usdStep}
            value={value.usd ?? ''}
            aria-label={t("BudgetControl.e9320701a9", { label: label })}
            onInput={event => setUsd(event.currentTarget.value)}
          />, value2: !usdOn && <small>{t("BudgetControl.a2916ea458")}</small> })}</label>
      </div>
      {usdOn && reportsCost === false && (<p class="settings-warning budget-warning">{costBlindWarning()}</p>)}
      {usdOn && unpricedCalls > 0 && (<p class="budget-warning">{rich("BudgetControl.6b6ec0bfe3", { unpricedCalls: unpricedCalls, value2: (unpricedCalls === 1) ? 'yes' : 'no' })}</p>)}
    </fieldset>
  )
}
