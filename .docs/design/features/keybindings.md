# Keyboard: chords, sequences, presets, and what each host can deliver

## What it is

The keyboard surface: a chord vocabulary that names physical keys, one-to-three-chord
sequences behind a leader, a rule list that can be scoped to a host, a platform and a
focus context, five shipped presets that are data rather than code, and a per-host
capability report that says what a chord will actually do instead of refusing it.

The scale it answers: **214 bindable commands.** Before this there were 26 default
bindings and no way to reach the rest without opening the palette.

## The three defects this replaced, because each one is a rule now

**Ctrl+Alt is AltGr.** 24 of the 26 previous defaults were `ctrl+alt+<key>`. Windows
and X11 both synthesise Ctrl+Alt for AltGr, so on a German, French, Polish, Spanish or
Nordic keyboard those chords fired while the user was typing an ordinary character.
No shipped preset may use Ctrl+Alt (`test_no_shipped_preset_binds_a_ctrl_alt_chord`),
and one a user chooses is reported rather than silently accepted.

**Those same defaults were dead on Linux.** `ctrl+alt+arrowleft/right` is GNOME and
KDE workspace switching and `ctrl+alt+t` opens a terminal, so `pane.next`,
`pane.previous` and `session.spawnShell` never reached the app there. The window
manager's grabs are now a per-platform table (`keychords.WM_RESERVED`), and Linux is
not an edge case: **there is no macOS or Linux desktop shell, so those platforms are
always browser-hosted.** The "which host?" and "which OS?" axes are not independent.

**The reserved-chord list was too strict, and provably.** It contained `ctrl+f` and
refused to let anyone bind it, while `Settings.tsx` was intercepting Ctrl+F
successfully in the same browser. It conflated two different things: chords a browser
never dispatches to the page at all, and chords the page receives and can suppress.
Roughly half the list was the second kind. Nothing is refused for being reserved any
more, with one exception below.

## The chord vocabulary

A **chord** is `ctrl+shift+k`: modifiers in one fixed order, then a key token. A
**binding** is one to three chords separated by spaces (`ctrl+shift+space p n`).

**Tokens come from `KeyboardEvent.code`, the physical key, never from `.key`.**
`.key` is what the active layout produced, and three things follow. A binding recorded
on Dvorak means the same physical key on QWERTY. Shifted punctuation is expressible at
all - `Ctrl+Shift+5` reports `key: '%'`, so a `.key`-derived chord could never match a
table written as `ctrl+shift+5`, which is exactly the shape tmux's `prefix %` needs.
And the label a reader sees is drawn separately, in their own platform's convention
(Apple documents ⌃⌥⇧⌘, which is not the storage order).

**Only the first chord of a sequence needs Ctrl, Alt or Meta.** This is the entire
practical argument for a leader and it is asserted rather than assumed: everything
after the leader is read while the sequence is armed, so it competes with nothing. A
prefix therefore costs **one** interceptable chord for the whole tree, where a flat
map costs one per command. The one relaxation on the first chord is a function key,
which produces no character - which is why VS Code Web uses F1 where Ctrl+Shift+P is
contested.

The cap is three chords. tmux and VS Code stop at two; Zellij's unlock-first preset
uses three, which is the deepest shape any preset here needs.

## Hosts, platforms, and the one hard refusal

A rule may carry `host` (`desktop` | `browser`), `platform` (`win` | `mac` | `linux`)
and `when`. **Resolution happens in the daemon**, not the browser, for the same reason
the experience-tier assignment does: a browser-computed answer would be a second copy
of the policy and the copy is what drifts. The client states what it is
(`GET /api/keybindings?host=…&platform=…`, `frontend/src/hostProfile.ts`) and gets one
answer computed for that keyboard.

The desktop shell publishes `window.__swemuxDesktopShell` on its own page
(`desktop_permissions.shell_report`), and **its absence is the signal**: a browser tab
never has it. The fact it carries is the one the keyboard needs - production WebView2
runs with pywebview's browser accelerators disabled, so that window receives Ctrl+T,
Ctrl+W and Ctrl+Tab where no browser tab will.

Four tables say what a chord costs, and only the first is a refusal:

| Table | What it means | Refused? |
|---|---|---|
| `APPLICATION_RESERVED` | the fixed UI-scale controls | **yes**, at every position in a sequence |
| `BROWSER_UNREACHABLE` | the page never receives the keydown | no - reported, and live in the desktop app |
| `BROWSER_CONTESTED` | the page receives it and can suppress the browser's own meaning | no - reported with what it costs |
| `WM_RESERVED[platform]` | the compositor takes it first | no - reported per platform |
| `TERMINAL_RESERVED` | what a shell in a pane means by it | no - reported, and scopable away with `when` |

Plus the AltGr hazard, which is any chord holding both Ctrl and Alt without Meta.

## Delivery is measured, not decreed

The tables above are a claim about somebody else's software and are wrong somewhere by
construction. So they are a starting point and the browser corrects them: Settings →
Input offers a probe (`frontend/src/hostKeyboardProbe.ts`, `KeyboardProbe.tsx`) that
asks the user to press each contested chord once. A chord that produces a `keydown`
is one the page can have.

Two honesty rules, and the measurement is worthless without them.

**An untested chord is `unknown`, never `blocked`.** "The browser ate it" and "nobody
pressed it" are the same signal from inside the page, so only a *tested* chord moves
the shipped answer. Treating silence as evidence would quietly hand back every chord
the probe was never run against.

**The correction outlives the tab.** It is stored in the per-device settings store
(`keyboard` domain, keyed by device class like every other one) and the daemon reads
it back in `_measured_unreachable`, so a later read resolves against what this browser
actually did rather than against the table.

**Keyboard Lock is offered, never assumed.** In JavaScript-initiated fullscreen a
Chromium tab can be handed Ctrl+T, Ctrl+W and Escape - the remote-access case the API
was specified for, and what swe-mux in a browser is. It is an explicit opt-in because
it takes those keys from the user's own browser and the only way out is Chrome's
two-second Escape hold. It arms and releases with fullscreen rather than being held.

## The rule list, and why it is not a map

`keybindings.json` is a list of rules, VS Code's shape: `keys`, `command`, plus
optional `host`, `platform`, `when` and `note`. A map could not hold it, because one
command wants several chords, one chord wants different commands on different
platforms, and a chord's meaning can depend on what is focused.

- **Later rules win**, which is the whole of "override": a preset's rules first, the
  user's appended after.
- **`command: ""` erases** a chord, so a preset binding can be deleted without editing
  the preset.
- **A chord cannot both fire and arm.** A leaf at a node that also has children is
  ambiguous, and resolving in favour of the leaf would delete the whole subtree behind
  it silently. The longer binding wins and the leaf is reported.
- The document records **which preset it was materialised from**, which is what lets a
  later release seed new defaults without an accumulating `V<N>_DEFAULT_KEYBINDINGS`
  constant per release - the previous format's mechanism, which could only ever grow.

### `when` is deliberately tiny

A `&&`-joined list of optionally `!`-negated flags from a closed set (`WHEN_FLAGS`).
No `||`, no parentheses, no comparisons. That is a total function over a known
vocabulary, cheap enough to evaluate on every keystroke, and it cannot grow into a
second expression language nobody can validate. An unknown flag reads as false, which
fails closed.

## The presets

Five, as JSON under `src/swe_mux/assets/keymaps/` - data, not code, so adding one is a
data edit and a user can write their own. Applying one is `POST /api/keymap-preset`,
an absolute rewrite of the document (never a merge), on an explicit press, after a
confirmation naming what it takes.

| Preset | Prefix | What it is |
|---|---|---|
| **swemux** | — | The default: a flat `Ctrl+Shift` set plus the leader |
| **tmux** | `Ctrl+B` | tmux's prefix and letters over panes, tabs and sessions |
| **vscode** | `Ctrl+K` | VS Code's chords where swe-mux has the same idea |
| **vim** | `Ctrl+W` | hjkl and window movement, with `Alt+hjkl` as the browser-safe mirror |
| **emacs** | `Ctrl+X` | `C-x 2/3/o/0/1`, `C-x b`, `M-x` |

Three rules hold the set together.

**Every preset includes the same leader tree** (`leader-tree.json`, ~200 bindings under
`Ctrl+Shift+Space`). Choosing tmux *adds* tmux; it never removes the route to a
command tmux has no opinion about. A preset that replaced the tree would have to
re-invent a mnemonic for every surface swe-mux has and nothing else does.

**A preset's own prefix is separate from the tree's leader**, because they would
collide otherwise and the collision is not hypothetical: tmux's `prefix p` is
"previous window" while the tree's `leader p` opens the pane group, and one of them
would lose its whole subtree. Two prefixes cost one extra chord and keep both.

**A preset states what it costs.** swe-mux is an *outer* shell, so any chord it claims
is claimed from whatever runs inside a pane. tmux, VS Code, Vim and Emacs each carry a
`warning`, shown in the picker *before* the choice and again on the first-run line.

There is deliberately no "browser-safe" preset. The host axis already answers that
question per binding, and a preset would be a second copy of the same policy.

## The default keymap

Flat chords use **`Ctrl+Shift`**, which every terminal emulator claims for itself
(GNOME Terminal, Konsole, Windows Terminal, kitty, Alacritty) precisely because
`Ctrl+<letter>` belongs to the shell inside. It is not AltGr and no window manager
grabs it. macOS additionally gets the same set mirrored onto `Cmd+Shift`; the leader
stays `Ctrl+Shift+Space`, which is free there because Cmd+Space is Spotlight.

The leader tree groups by the registry's own categories, and the group letter is the
one the which-key overlay prints: `p` panes, `s` sessions, `w` projects, `v` views,
`n` notes, `t` terminal, `i` input, `c` clipboard, `g` git, `h` history, `x` voice,
`d` the side panel, `f` focus regions, `r` resize, `m` move a tab.

**The which-key overlay is not optional.** With ~200 commands behind one prefix, a
leader without it is a memory test. It appears after 450 ms so fluent use never draws
it, and it never takes focus, because the sequence is still being typed.

## What the app can be told to do

The registry gained the vocabulary "navigate the whole UI" actually needs. Before
this, `pane.next`/`pane.previous` was the only pane movement (unusable past two panes),
and there was no way to move keyboard focus *between* regions at all - only within
them.

- `pane.focus/swap/resize/moveTab{Left,Right,Up,Down}`, `pane.close`, `pane.detach`
- `focus.terminal/sidebar/drawer/tabBar/composer`, `focus.next`, `focus.previous`
- `session.nextInProject`, `session.previousInProject`, `tab.activate(1..9)`
- `palette.commands/sessions/projects/files`

Resize is named after **where the divider goes** (tmux's `resize-pane -L/-R/-U/-D`).
"Grow my pane" cannot be said without the reader knowing which side of a split they
are on, and they cannot see that.

A focus region that is closed is **opened** first rather than skipped: "focus the side
panel" from a keyboard means "put me in the side panel", and refusing because it
happens to be shut is useless exactly when it is most wanted.

## The palette has four scopes

`>` commands, `@` sessions, `#` Projects, `:` files - VS Code's prefixes, named in the
UI rather than implied, because nobody discovers a prefix syntax by accident.

They exist because `searchCommands` scored a command's label, id and category, so the
single most common navigation in a fleet UI - "go to that session" - could not be
answered by the palette at all. Sessions and Projects need no new data (`fleetCommands`
already registers one command per row); files are fetched from
`/api/projects/{id}/search?mode=names` and exist only while the query does.

## The registry and the app must describe each other

They had drifted **both ways**, silently, and each direction has its own symptom.
About forty commands the palette offered were not in `KEYBINDING_COMMANDS`, so they
could not be bound to a chord or a gesture at all. Five ids were registered and
implemented nowhere (`projects.open`, `pane.detach`, `pane.swapNext`, `stack.tabLeft`,
`stack.tabRight`), so they appeared in the shortcut editor, accepted a chord and did
nothing - `pane.detach` even had a button in the pane menu wired to it.

Neither is visible from either side alone, so `tests/test_keybinding_registry.py`
reads the frontend's own command literals and fails on either direction. Its
`GENERATED` allowlist (for ids the app builds in a loop) is itself checked against the
registry, so it cannot become the place a retired id hides.

## Who owns a keystroke

Two handlers see every key: xterm's `attachCustomKeyEventHandler`, which runs first and
decides whether the byte reaches the PTY, and App's window listener, which dispatches.
With a flat map each could answer independently. With sequences they cannot - the
terminal has no way to know that `p` is the second half of `leader p` rather than a
letter the shell wants - so the sequence state lives in exactly one place
(`frontend/src/keymapDispatch.ts`) and the split is load-bearing:

- `claims(chord)` is a **pure question**. The pane calls it to decide whether to
  swallow the key; calling it twice must equal calling it once.
- `advance(chord)` is the **single mutation**, called once per keydown by App.

Getting that backwards would consume the first chord twice and make every second
keystroke of a sequence mysterious.

Three consequences worth stating. An **armed sequence swallows its own abandonment** -
forwarding the stray key would type a character into a terminal the user believed was
listening for a shortcut, which is the one outcome nobody can attribute. A **modifier
held on its own never advances** the machine, or reaching `leader ctrl+w` would end
the sequence. And a **keymap that changes under an armed sequence drops it**, because
the pending chords now mean something else, or nothing.

The pane no longer receives the keymap as a prop at all, which also stopped a keymap
edit from tearing down and rebuilding every terminal.

## First run

The preset is **one line on the page that already exists** - the experience-tier step
inside `HarnessSetup.tsx` - rather than a fourth first-run surface. `firstRunSurface()`
arbitrates three, the tier step was folded in for exactly this reason, and a keymap is
a defaults choice of the same shape. Skipping leaves `keymap_preset` empty, which is
what a fresh install already behaves as; applying the default preset would rewrite a
file for no change, so it does not.

## Key files

- `src/swe_mux/keychords.py` - chord syntax, the host/platform tables, capability reports
- `src/swe_mux/keybindings.py` - the command registry, `when`, rules, resolution
- `src/swe_mux/keymaps.py`, `src/swe_mux/assets/keymaps/*.json` - the presets
- `src/swe_mux/routes/settings.py` - `GET/PUT /api/keybindings`, `POST /api/keymap-preset`
- `frontend/src/keys.ts` - the tokenizer and the labels
- `frontend/src/keymap.ts`, `frontend/src/keymapDispatch.ts` - the trie and the one owner of sequence state
- `frontend/src/hostProfile.ts`, `frontend/src/hostKeyboardProbe.ts`, `frontend/src/KeyboardProbe.tsx`
- `frontend/src/WhichKey.tsx` - the leader overlay
- `tests/test_keybindings.py`, `tests/test_keybinding_registry.py`,
  `frontend/test/keys.test.ts`, `frontend/test/keymap.test.ts`,
  `frontend/test/keymapDispatch.test.ts`, `frontend/test/hostProfile.test.ts`

## Relates to

- `design/features/ui.md` - the Settings editor, the palette, focus and overlays
- `design/features/first-run.md` - the tier page the preset line lives on
- `design/features/desktop-shell.md` - why the desktop app has a different keyboard
- `design/features/terminal-input.md` - what a pane does with a key swe-mux did not claim
- `design/features/workspace-layout.md` - the pane vocabulary the directional commands drive
