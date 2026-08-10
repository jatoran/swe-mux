// The sidebar's provider-quota breakdown inside the real shell.
//
// The `.app-shell` wrapper is the point of the harness, not scaffolding: that
// selector is what forces `--ui-font-size` onto every descendant with
// `!important`, so the 8px monospace `.account-summary > button` asks for is
// never what renders. Measuring this markup anywhere else measures a font the
// app does not draw, which is exactly how the column tracks came to be cut two
// scale steps too narrow for their own content.
//
// The markup mirrors `quotaGrid` in `ProviderAccounts.tsx`; the *strings* come
// from the real display helpers, since a new window kind or a changed duration
// format is what would silently outgrow a track.
import { render } from 'preact'
import {
  accountAbbreviation, quotaGridSegments, type ProviderQuotaWindows,
} from '../../src/providerAccountDisplay'
import '../../src/style.css'

const NOW = 1_770_000_000
const at = (hours: number) => NOW + hours * 3600

// Chosen for width, not realism: the longest reset string the formatter can
// produce (`23h59m`, `13d23h` — six characters) above the longest percentage
// (`100%`), plus the unmeasured `—` and the named `Fable` window.
const CASES: Array<{ account: string; provider: string; windows: ProviderQuotaWindows }> = [
  {
    account: 'jordan.hale@example.com', provider: 'claude',
    windows: {
      session: { used_percent: 97, resets_at: at(23.98) },
      weekly: { used_percent: 100, resets_at: at(24 * 13 + 23) },
      fable: null,
    },
  },
  {
    account: 'work', provider: 'codex',
    windows: {
      session: { used_percent: 8, resets_at: at(4.53) },
      weekly: { used_percent: 61, resets_at: at(24 * 2 + 6) },
      fable: { used_percent: 3, resets_at: at(9) },
    },
  },
  {
    account: '', provider: 'claude',
    windows: {
      session: { used_percent: 0, resets_at: null },
      weekly: { used_percent: 5, resets_at: at(0.74) },
      fable: null,
    },
  },
]

render(
  <div class="app-shell">
    <div class="sidebar">
      <div class="sidebar-status">
        <div class="account-switcher">
          <div class="account-summary">
            {CASES.map((item, index) => {
              const segments = quotaGridSegments(item.windows, NOW)
              return <button key={index} class="tracked" data-case={String(index)}>
                <span class={`quota-grid quota-grid-${segments.length}`}>
                  <span class="quota-grid-column quota-grid-identity">
                    <i class={`provider-glyph ${item.provider}`} aria-hidden="true">◆</i>
                    <strong class="quota-account">{accountAbbreviation(item.account)}</strong>
                  </span>
                  {segments.map(segment => (
                    <span class="quota-grid-column quota-grid-metric" key={segment.key}>
                      <small>{segment.heading}</small>
                      <i class={`quota-window usage-${segment.band}`}>{segment.text}</i>
                    </span>
                  ))}
                </span>
              </button>
            })}
          </div>
        </div>
      </div>
    </div>
  </div>,
  document.querySelector('#root')!,
)
