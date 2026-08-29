"""The hand-authored content of the documentation browser at `/docs/`.

`tools/build.py` owns rendering, the shell, the search index, and the page
registry; this module owns the words. The split is the same one `content/`
already has with the generated pages: prose that a person writes lives apart
from the code that lays it out.

**Nothing here is lifted from `.docs/`, and that is the point.** Those are
internal design documents: they are written for whoever maintains a subsystem
next, they name internal phases and incident dates, and they state invariants
that read as commitments on a public page. Publishing them wholesale would be
worse than not publishing them, and publishing one thin stub per document would
be worse still - a stub costs a click and answers nothing.

So each page below is written for somebody **using** swe-mux, sourced from the
design documents and from the code, and it is a page only when there is enough
to say. Topics that could not be written well from the repository are left out
rather than stubbed.

Three rules keep these from rotting, and they are the same three the quick
starts already carried:

- **Every command is one that exists.** The install commands are the ones
  executed against the published 0.1.0 wheel (`site/README.md` section 7); the
  keybindings are `src/swe_mux/keybindings.py`'s own defaults; the CLI
  subcommands and flags are `src/swe_mux/cli.py`'s and
  `src/swe_mux/__main__.py`'s; the data-directory paths are
  `src/swe_mux/config.py`'s.
- **Every surface named is a real tab.** Settings tabs are
  `frontend/src/settingsTabs.ts` and its `settingsSubpages`; drawer tabs are
  `frontend/src/drawerTabs.ts`.
- **No page links to a `.docs/**.md` blob.** A reader who wants to know how a
  feature works is owed a page, not a redirect into raw Markdown written for
  maintainers. `tools/check.mjs` asserts it. The one page that is explicitly
  maintainer material (`contributing`) links to the repository's own root
  documents, which are written to be read by a contributor.

## The block vocabulary

A page is a list of `(kind, value)` blocks. Keeping content declarative rather
than as HTML strings is what lets one definition produce three things that
cannot disagree: the rendered page, the in-page heading list, and the search
index's text. A page written as raw markup would need its search text written a
second time, and the copy is what drifts.

| Kind | Value | Renders as |
| --- | --- | --- |
| `h2` | heading text | a section head, with an `id` derived from the text |
| `p` | inline HTML | a prose paragraph |
| `note` | inline HTML | a quieter aside paragraph |
| `ul` | list of inline HTML | a marker list |
| `steps` | list of inline HTML | a numbered list |
| `flat` | list of `(label, inline HTML)` | a label/description row list |
| `code` | text | a preformatted block, escaped |
| `table` | `(headers, rows)` | a table |
| `proof` | inline HTML | the "it worked when" line |

Inline HTML is trusted and written by hand here; `<b>`, `<em>`, `<code>`,
`<kbd>` and `<a>` are what it uses. That includes table headers and cells, which
were escaped in the first shape of this and should not have been: a command
reference wants `<code>--export</code>` in a cell, and escaping published the
tag. **Page titles, section titles, and `flat` labels are the escaped ones**,
because those are plain-text nouns and are also used as link text, sidebar
entries, and search-index fields where markup would be meaningless.

A consequence worth stating, since it is the one thing a careless edit breaks: a
literal `<` or `&` in trusted content has to be written as an entity. `build.py`
would happily emit it and a browser would happily misread it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Every repository URL any page here builds on. `build.py` owns the constant;
# this module takes it as a literal prefix so it stays importable on its own.
REPO = "https://github.com/jatoran/swe-mux"
BLOB = f"{REPO}/blob/master"

Block = tuple[str, object]


@dataclass(frozen=True)
class Page:
    """One documentation page, served at `/docs/<slug>/`."""

    slug: str
    title: str
    description: str
    lede: str
    blocks: list[Block] = field(default_factory=list)


@dataclass(frozen=True)
class Section:
    """One sidebar group. A section is never a page of its own."""

    title: str
    pages: list[Page]


# --------------------------------------------------------------- getting started

INSTALL = Page(
    slug="install",
    title="Install",
    description=(
        "Install swe-mux from PyPI with uv, pipx, or pip, on Windows, Linux, or macOS. "
        "What each method leaves you with, and the two things no method does."
    ),
    lede=(
        "swe-mux is on PyPI. The wheel is <code>py3-none-any</code> - pure Python, no compiled "
        "extensions - and it already carries the built frontend, so installing needs no checkout "
        "and no Node."
    ),
    blocks=[
        ("h2", "Before you start"),
        (
            "ul",
            [
                "<b>Python 3.12 or newer.</b> Check with <code>python --version</code>.",
                "<b>An installer.</b> <code>uv</code> is the recommended one, <code>pipx</code> "
                "works the same way, and <code>pip</code> works with a caveat covered below.",
                "<b>At least one agent CLI, already installed and logged in.</b> swe-mux does not "
                "install, manage, authenticate, or proxy them. If you have none yet, install "
                "Claude Code, Codex CLI, or opencode first and log into it there.",
                "<b>Node is not required.</b> Node 22.6 or newer is needed only if you are "
                "building swe-mux from a checkout.",
            ],
        ),
        ("h2", "Install it"),
        (
            "p",
            "Every method below writes the same three commands: <code>mux</code> (the CLI), "
            "<code>muxd</code> (the daemon), and <code>swe-mux</code> (the desktop window and "
            "tray). Run exactly one.",
        ),
        (
            "code",
            "# Recommended. Isolated environment, all three commands on PATH globally.\n"
            "uv tool install swe-mux\n"
            "\n"
            "# On Windows, take the desktop extra: it is what adds the window and the tray icon.\n"
            'uv tool install "swe-mux[desktop]"\n'
            "\n"
            "# The same isolated, on-PATH install, without uv.\n"
            "pipx install swe-mux\n"
            "\n"
            "# NOT the same act. Installs into whichever environment is currently active and\n"
            "# puts nothing on PATH globally, so `mux` works only inside that environment.\n"
            "pip install swe-mux",
        ),
        ("h2", "Two things no install does"),
        (
            "flat",
            [
                (
                    "No shortcut, no Start Menu entry",
                    "Wheels have no post-install hook and pip runs no install-time code, so this "
                    "is structural rather than a step somebody forgot. swe-mux starts from a "
                    "terminal. On Windows you can create the shortcuts afterwards with "
                    "<code>mux install-shortcut</code>, which is idempotent and has a "
                    "<code>--remove</code>.",
                ),
                (
                    "No agent CLI is installed or logged in",
                    "That stays your own arrangement with each vendor. swe-mux finds the CLIs "
                    "already on your machine and runs them under your own subscription.",
                ),
            ],
        ),
        ("h2", "The desktop extra, on Windows"),
        (
            "p",
            "Without <code>[desktop]</code> you still get a <code>swe-mux</code> command, and it "
            "fails on a missing import rather than opening a window. The extra wants the WebView2 "
            "Runtime, which recent Windows builds already have.",
        ),
        (
            "note",
            "It is Windows-only by declaration: <code>pystray</code> and <code>pywebview</code> "
            "both carry a <code>win32</code> platform marker, so on Linux and macOS the extra "
            "resolves to nothing and the daemon plus a browser is the whole product.",
        ),
        ("h2", "Start it"),
        (
            "code",
            "muxd                          # the daemon\n"
            "# then open http://127.0.0.1:8765\n"
            "\n"
            "swe-mux                       # Windows, with the desktop extra: the same thing in a window",
        ),
        (
            "proof",
            "<code>mux doctor</code> exits 0. It is read-only and reports on the daemon, the "
            "supervisor, the frontend build, the agent CLIs it can detect, the tailnet listener, "
            "and the background loops. It is the command that tells installed from working.",
        ),
        ("h2", "If nothing is on your PATH afterwards"),
        (
            "p",
            "This is the ordinary outcome of <code>pip install</code>, and its "
            "<code>WARNING: The scripts ... are installed in '...' which is not on PATH</code> "
            "scrolls past unread.",
        ),
        (
            "code",
            "# The daemon, needing no PATH setup at all: `python -m swe_mux` is exactly `muxd`.\n"
            "python -m swe_mux\n"
            "\n"
            "# Where the three executables went (a `Scripts` directory on Windows).\n"
            "python -c \"import sysconfig; print(sysconfig.get_path('scripts'))\"\n"
            "\n"
            "# Every file this install wrote, those three included.\n"
            "pip show -f swe-mux",
        ),
        ("h2", "Platform support, stated exactly"),
        (
            "table",
            (
                ["Host", "What is proven", "What is not"],
                [
                    [
                        "Windows 10 or 11",
                        "The proving platform. The full gate runs there in CI, including the real "
                        "ConPTY integration tests and the browser renderer suite, and it is the "
                        "only host the desktop app ships on.",
                        "-",
                    ],
                    [
                        "Linux",
                        "The daemon plus a browser, on a required CI leg. A daemon starts, serves "
                        "a real terminal, and exits cleanly there on every push.",
                        "There is no Linux desktop app, by design.",
                    ],
                    [
                        "macOS",
                        "The wheel installs and the CLI runs, checked on every push. The suite "
                        "runs there too.",
                        "That CI leg is still allowed to fail, so treat macOS as unproven and "
                        "expect to debug.",
                    ],
                ],
            ),
        ),
        (
            "note",
            "The honest boundary across all three: <b>no CI job on any host starts a daemon from "
            "the published wheel</b>. Installing and running the CLI is proven everywhere. That "
            "is not the same claim as working end to end.",
        ),
        ("h2", "Upgrading, and from a checkout"),
        (
            "code",
            "uv tool upgrade swe-mux       # or: pipx upgrade swe-mux\n"
            "\n"
            "# Run from a checkout instead - what you want if you are changing swe-mux itself.\n"
            "git clone https://github.com/jatoran/swe-mux\n"
            "cd swe-mux\n"
            "uv sync --extra desktop\n"
            "npm --prefix frontend ci        # only the source flow needs Node\n"
            "npm --prefix frontend run build # a fresh clone serves no UI until this runs once\n"
            "uv run --extra desktop swe-mux",
        ),
        (
            "note",
            "The frontend bundle is git-ignored build output, so a fresh clone serves a blank "
            "page rather than an error until that build has run once. "
            '<a href="../troubleshooting/">Troubleshooting</a> covers the symptom.',
        ),
        ("h2", "On-device speech is a separate extra"),
        (
            "p",
            "Local text-to-speech and local dictation are <code>--extra voice-local</code>, "
            "roughly 400 MB of wheels and model machinery. The Windows desktop bundle always "
            "carries it. Without it swe-mux speaks through the operating system's voice engine "
            "and dictates through the browser, which is a working configuration rather than a "
            "degraded one.",
        ),
    ],
)

FIRST_SESSION = Page(
    slug="first-session",
    title="Your first session",
    description=(
        "Create a Project, open a terminal, and promote it into an agent session by typing the "
        "command you already type. What to expect the first time."
    ),
    lede=(
        "There is no special ritual for starting an agent. You open a terminal and type the "
        "command you already type, and the terminal you are standing in becomes an agent session "
        "in place."
    ),
    blocks=[
        ("h2", "Create a Project first"),
        (
            "p",
            "Nothing works until there is one, and nothing is spawned until you ask for it. A "
            "Project is a folder swe-mux is pointed at, and it is what sessions, layout, notes, "
            "files, history, and per-Project settings all bind to. Point your first one at a "
            'repository you already work in. <a href="../projects/">Projects</a> covers the rest.',
        ),
        ("h2", "Open a terminal, then type your CLI"),
        (
            "steps",
            [
                "With a Project selected, press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd>. "
                "That opens a real terminal at the Project's root - a plain shell, nothing more.",
                "Type <code>claude</code>, <code>codex</code>, or whichever CLI you use, exactly "
                "as you normally would.",
                "<b>The terminal is promoted in place.</b> Same pane, same scrollback, now "
                "carrying a transcript, a status, a prompt queue, and a context meter. swe-mux "
                "puts its own launchers first on that terminal's PATH.",
                "Give it something to do, and watch the pane's status strip. "
                "<em>Working</em>, <em>ready</em>, <em>awaiting</em>, and <em>blocked</em> mean "
                "the same thing whichever vendor's CLI produced them.",
                "While it is mid-turn, open the <b>Queue</b> tab in the utility drawer and stage "
                "your next message. It is durable and head-of-line, and <b>automatic delivery is "
                "off by default</b>: a queued message waits for you to send it.",
            ],
        ),
        (
            "proof",
            "the pane's status strip stops saying <em>working</em>, and the Transcript tab has "
            "something in it. A session sitting on <em>awaiting</em> is the agent waiting on you "
            "rather than a stall.",
        ),
        ("h2", "The Run menu, for everything you did not want to type"),
        (
            "p",
            "It starts an agent, a shell, a worktree session, or a task discovered in the "
            "repository: VS Code tasks, root <code>package.json</code> scripts, and "
            "<code>.swe-mux/actions.toml</code>. An imported task stays inert until its exact "
            "current bytes are approved, and any edit revokes that approval - so a task file that "
            "changed under you cannot run on yesterday's permission.",
        ),
        ("h2", "A CLI swe-mux has never heard of still works"),
        (
            "p",
            "swe-mux is a terminal multiplexer before it is anything else. Any shell, any TUI, any "
            "CLI runs in a real pseudoterminal exactly as it does outside. A harness the registry "
            "does not know works perfectly; what it does not get is the layer on top - normalized "
            "status, transcripts, cross-vendor history, account switching. "
            '<a href="../sessions/">Sessions and harnesses</a> has the detail.',
        ),
        ("h2", "Checking swe-mux can see your CLI"),
        (
            "code",
            "mux harnesses        # every harness in the registry, with its detection state",
        ),
        (
            "p",
            "Settings, Harnesses shows the same thing plus the executable each one resolved to. "
            "If it resolved to the wrong binary, or to none, set the path for that harness there. "
            "That is the usual fix on a machine carrying several installs of the same CLI.",
        ),
        ("h2", "The two chords worth learning now"),
        (
            "table",
            (
                ["Chord", "What it does"],
                [
                    ["Ctrl+Alt+T", "A terminal at the current Project's root"],
                    ["Ctrl+Alt+P", "The command palette, which reaches everything else"],
                ],
            ),
        ),
        (
            "note",
            'The full default set is on the <a href="../keyboard/">keyboard reference</a>, and '
            "every one of them is rebindable.",
        ),
    ],
)

PHONE = Page(
    slug="phone",
    title="Reach it from your phone",
    description=(
        "The phone client is the same application over your own Tailscale tailnet: no relay, no "
        "swe-mux login. How to set it up, and what the access boundary actually is."
    ),
    lede=(
        "The phone client is the same application, not a companion. It goes over your own tailnet "
        "with no relay in the path and no swe-mux account, because there is no swe-mux server for "
        "an account to live on."
    ),
    blocks=[
        ("h2", "Setting it up"),
        (
            "steps",
            [
                "Install Tailscale on the host machine and on the phone, on the same tailnet.",
                "Leave the daemon running. It listens on loopback and on the machine's detected "
                "Tailscale address; <code>muxd --local-only</code> is how you stop it doing the "
                "second one.",
                "On the phone, open the machine's <code>.ts.net</code> hostname over HTTPS.",
                "Turn <b>Use Tailscale DNS</b> on, and leave Android's Private DNS off or "
                "automatic. The certificate is bound to the hostname, so the raw "
                "<code>100.x</code> address cannot serve HTTPS at all.",
                "Install it to the home screen when the browser offers. It is a progressive web "
                "app, so it gets its own window and can receive push notifications.",
            ],
        ),
        (
            "proof",
            "the workspace renders on the phone with your Projects in it, and a terminal pane "
            "accepts input.",
        ),
        ("h2", "HTTPS is not optional for the microphone or the clipboard"),
        (
            "p",
            "Browsers restrict both outside a secure context. swe-mux puts Tailscale Serve on "
            "port 443 in front of the daemon for exactly this, and starts it at boot. Port 443 "
            "rather than the daemon's own port is deliberate: the daemon binds its port on the "
            "tailnet address directly for the plain-HTTP fallback, and a Serve on the same port "
            "would collide with it.",
        ),
        ("code", "tailscale serve status        # confirm it proxies to the daemon's real port"),
        ("h2", "Understand the access boundary before you leave it running"),
        (
            "p",
            "<b>Tailscale policy is the entire access boundary.</b> There is no separate swe-mux "
            "login, so any device your tailnet admits to that listener has terminal and "
            "code-execution authority on the host, equal to the account running the daemon.",
        ),
        (
            "p",
            "Binding <code>0.0.0.0</code>, a LAN interface, port forwarding, and Tailscale Funnel "
            "are <b>unsupported configurations rather than discouraged ones</b>. They put a "
            "terminal on a network that has no policy in front of it.",
        ),
        ("h2", "What parity means here"),
        (
            "p",
            "swe-mux has no feature that exists on the desktop and not on the phone. The only "
            "desktop-only code paths are hidden terminal pre-warming, the collapsed sidebar rail, "
            "and keyboard chords a phone cannot produce.",
        ),
        (
            "p",
            "One session can be attached from several devices at once. Exactly one connection may "
            "write, and the terminal size is arbitrated rather than fought over, so the phone does "
            "not reflow the desktop out from under you. The phone renders a single-pane projection "
            "of the same workspace tree rather than a second layout.",
        ),
        ("h2", "Development servers, without a raw port"),
        (
            "p",
            "A local dev server your session started is proxied through the daemon's own URL, hot "
            "module reload included, so the phone never needs to reach a raw port on the host. "
            '<a href="../notes-files/">Notes, files, and previews</a> covers the Preview tab.',
        ),
    ],
)

AGENT_SETUP = Page(
    slug="agent-setup",
    title="Have an agent set it up",
    description=(
        "Paste one line into Claude Code, Codex, or any agent that can fetch a URL, and it reads "
        "a guide written for walking a person through installing swe-mux."
    ),
    lede=(
        "If you already run a coding agent, the fastest install is to hand the job to it. There is "
        "a guide written for that audience specifically, and it is plain Markdown rather than a "
        "page, so an agent gets the text and not a layout."
    ),
    blocks=[
        ("h2", "The line to paste"),
        (
            "code",
            "Help me understand and set up swe-mux. Read https://swemux.dev/agent-guide.md "
            "first, then walk me through it step by step.",
        ),
        (
            "note",
            'The guide is <a href="../../agent-guide.md"><code>https://swemux.dev/agent-guide.md</code></a>. '
            "It tells your agent to ask before it installs anything.",
        ),
        ("h2", "What the guide covers"),
        (
            "ul",
            [
                "What swe-mux is, and the three properties worth explaining before anything is "
                "installed.",
                "The prerequisites to check, and which failures should stop the install.",
                "Every install method, what each one leaves behind, and the two things none of "
                "them do.",
                "First run, and <code>mux doctor</code> as the verification step.",
                "The concepts to explain and the order to explain them in, which is deliberately "
                "not the order a feature list would use.",
                "Where configuration and data live on each host.",
                "The phone setup, and the access boundary stated plainly.",
                "Exactly what leaves the machine, in the same terms "
                '<a href="../data/">this reference</a> uses.',
            ],
        ),
        ("h2", "What it tells the agent about itself"),
        (
            "p",
            "The last section of the guide is rules for the agent rather than for you, and they "
            "are the part worth holding it to:",
        ),
        (
            "flat",
            [
                (
                    "Verify, do not assume",
                    "After each stage, run the command that proves it worked and read the real "
                    "output. <code>mux doctor</code> is that command for the install as a whole.",
                ),
                (
                    "Do not invent commands or flags",
                    "A command that does not exist costs a person whose install is already broken "
                    "the one thing they came for.",
                ),
                (
                    "Do not run destructive commands",
                    "Nothing in a swe-mux install needs elevation, and nothing needs an existing "
                    "directory removed.",
                ),
                (
                    "Say what was skipped",
                    "If it skipped the phone step or the desktop extra, it should say so and say "
                    "what you gave up.",
                ),
                (
                    "Your agent CLIs are not its to reconfigure",
                    "swe-mux reads their state. Changing their settings, accounts, or transcripts "
                    "is out of scope for a setup pass.",
                ),
            ],
        ),
        ("h2", "It names its own authorities"),
        (
            "p",
            "The guide states which sources win when it disagrees with them, and it carries the "
            "date it was revised and the version it was checked against. That is what makes it "
            "safe for it to go stale: a reader is told where the authority is rather than being "
            "left to find out.",
        ),
    ],
)

# ---------------------------------------------------------------------- concepts

PROJECTS = Page(
    slug="projects",
    title="Projects and Groups",
    description=(
        "A Project binds a folder to sessions, layout, notes, files, history, and its own "
        "settings. What that means in practice, and which switches are per-Project."
    ),
    lede=(
        "A <b>Project</b> is a folder swe-mux is pointed at, and it is the thing everything else "
        "binds to. Nothing works until there is one."
    ),
    blocks=[
        ("h2", "What a Project owns"),
        (
            "flat",
            [
                (
                    "Sessions",
                    "Every terminal and agent session is spawned into a Project and stays in its "
                    "sidebar group for life. Stepping into a worktree does not re-home a session: "
                    "a worktree is the same Project as the tree it was cut from.",
                ),
                (
                    "Workspace layout",
                    "Panes, tabs, and split geometry are durable per-Project state on the desktop. "
                    "The phone renders a projection of the same tree rather than a second layout.",
                ),
                (
                    "Notes and files",
                    "A Project-owned note collection and a bounded file tree with ignore rules. "
                    "Both open into a pane rather than only into the drawer.",
                ),
                (
                    "History",
                    "Conversations are indexed against the Project they ran in, so a search can be "
                    "scoped to one repository or run across all of them.",
                ),
                (
                    "Its own settings",
                    "Most switches that decide how much swe-mux does live here rather than "
                    "globally. That is what lets the control plane be on for one repository and "
                    "off for every other.",
                ),
            ],
        ),
        ("h2", "Groups are optional organisation above Projects"),
        (
            "p",
            "A Group is sidebar structure and nothing more. It does not change what a Project "
            "owns, and a Project does not need one.",
        ),
        ("h2", "Per-Project rather than global, and why"),
        (
            "p",
            "The control plane, automation observers, the scan timeline, the code graph, and the "
            "land queue are all <b>off by default and enabled per Project</b>. The reason is that "
            "they cost something - a model call, a scan budget, a graph build - and most of a "
            "person's Projects are not the one they want that spent on.",
        ),
        (
            "p",
            "A second rule sits on top of that one: <b>a gate can only ever turn something "
            "on</b>. Many surfaces may switch a thing on in place; exactly one editor may switch "
            "it off. For automation that editor is the Automation dashboard's Policy tab, which "
            "holds both the install-wide ceiling and every per-Project opt-in.",
        ),
        ("h2", "The Project context card"),
        (
            "p",
            "A Project can carry a short user-owned description of what it is and how it is meant "
            "to be worked in. It is a fixed file with an editor and bounds, it is yours rather "
            "than generated, and control-plane features that need context read it instead of "
            "guessing.",
        ),
        ("h2", "What a Project does not do"),
        (
            "ul",
            [
                "It does not copy, move, or rewrite anything in the folder it points at.",
                "It does not commit, and it does not run anything you did not ask for.",
                "Registering a Project spawns no session. Nothing starts until you start it.",
                "<code>.swe-mux/</code> inside a checkout is per-machine state. Nothing in it is "
                "meant to be committed, and swe-mux writes a <code>.gitignore</code> that says so "
                "- unless you already wrote one, which is never overwritten.",
            ],
        ),
        (
            "note",
            "Switching Projects: <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>1</kbd> through "
            "<kbd>9</kbd> activate the first nine in sidebar order, and the command palette "
            "reaches the rest.",
        ),
    ],
)

SESSIONS = Page(
    slug="sessions",
    title="Sessions, terminals, and harnesses",
    description=(
        "A session is one pseudoterminal with a process in it. What swe-mux owns, what survives a "
        "restart, and what a recognised harness gets that an unrecognised one does not."
    ),
    lede=(
        "A <b>session</b> is one pseudoterminal with a process in it. Everything else swe-mux does "
        "is built on owning that pseudoterminal rather than on wrapping the program inside it."
    ),
    blocks=[
        ("h2", "Two kinds, and one difference between them"),
        (
            "flat",
            [
                (
                    "Shell session",
                    "A real terminal running your shell. PowerShell, CMD, bash, a WSL distro "
                    "shell, anything. It behaves exactly as it does outside swe-mux.",
                ),
                (
                    "Agent session",
                    "The same thing, with a harness swe-mux recognises running inside it. The "
                    "difference is entirely the layer on top: normalized status, a transcript, a "
                    "prompt queue, a context meter, cross-vendor history, and account switching.",
                ),
            ],
        ),
        (
            "p",
            "A shell becomes an agent session <b>in place</b> when you type the CLI's command, "
            "because swe-mux puts its own launchers first on that terminal's PATH. Same pane, "
            "same scrollback. There is no separate start-an-agent action to learn, and the Run "
            "menu's agent entries are a convenience rather than the mechanism.",
        ),
        ("h2", "A CLI the registry has never heard of still works"),
        (
            "p",
            "swe-mux is a terminal multiplexer first. Anything that runs in a terminal runs here "
            "unchanged. An unrecognised CLI gets a real pseudoterminal and nothing else; a "
            "recognised one gets the layer. A harness list is not a support list.",
        ),
        ("h2", "Sessions that outlive the application"),
        (
            "p",
            "A supervisor process, separate from the daemon and from the UI, can hold every "
            "pseudoterminal. With it on, restarting the daemon or rebuilding the desktop app "
            "leaves the agents working, and reconnecting replays only the bytes you missed rather "
            "than the whole scrollback.",
        ),
        (
            "note",
            "It ships <b>off</b>. Turn it on in Settings, Terminals. With it off, in-process "
            "spawning is the fallback and a daemon restart reaps every session - which is why the "
            "restart endpoint refuses outright unless it is forced.",
        ),
        ("h2", "What survives a crash is a separate mechanism"),
        (
            "p",
            "Cold recovery is independent of the supervisor and ships <b>on</b>. A durable "
            "registry row is written when a session is registered, carrying an open marker; a "
            "clean shutdown closes it and a crash closes nothing, which is the entire signal. "
            "Sessions whose daemon and terminal owner both died come back as visible, dead, "
            "resumable rows rather than vanishing.",
        ),
        (
            "p",
            "Terminal checkpoints are the optional half: a bounded slice of scrollback per "
            "session, so a recovered row is readable rather than merely present. Setting the "
            "checkpoint size to zero keeps the registry - which is the part that brings sessions "
            "back - and stores no terminal bytes.",
        ),
        ("h2", "One session, several devices"),
        (
            "p",
            "A session can be attached from the desktop and the phone at once. <b>Exactly one "
            "connection may write</b>, and the terminal size is arbitrated rather than fought "
            "over, so a phone attaching does not reflow a desktop pane. Which device counts as "
            "the one you are at is decided once, application-wide, rather than guessed separately "
            "by each feature.",
        ),
        ("h2", "Launch profiles"),
        (
            "p",
            "A named shell or agent launch: which executable, which arguments, which working "
            "directory. A WSL distro shell is a profile like any other. Profiles are edited in "
            "Settings, Harnesses and listed by <code>mux profiles</code>.",
        ),
        (
            "note",
            "The WSL agent bridge reports whether the distro can actually reach the daemon rather "
            "than assuming it. Its failure mode is silence: a bridged agent that cannot reach the "
            "daemon runs perfectly and never reports.",
        ),
        ("h2", "An ended session stays readable"),
        (
            "p",
            "A session whose process exited does not disappear from the sidebar. It stays as a "
            "readable, resumable row, because the thing you most often want after an agent "
            "finishes is to read what it did.",
        ),
    ],
)

STATUS = Page(
    slug="status",
    title="Session status",
    description=(
        "Working, ready, awaiting, blocked: one status vocabulary across every vendor's CLI, how "
        "it is read, and why awaiting is the one to watch."
    ),
    lede=(
        "Every session carries one of a small set of states, and they mean the same thing whichever "
        "vendor's CLI produced them. This is the layer the rest of the fleet view is built on: you "
        "cannot rank what needs you if you cannot tell what each session is doing."
    ),
    blocks=[
        ("h2", "The vocabulary"),
        (
            "table",
            (
                ["State", "What it means", "What to do"],
                [
                    [
                        "working",
                        "The agent is mid-turn and producing output.",
                        "Nothing. Stage your next message in the Queue tab if you have one.",
                    ],
                    [
                        "ready",
                        "The turn finished and the agent is waiting for input.",
                        "Type, or send a queued message.",
                    ],
                    [
                        "awaiting",
                        "The agent is blocked on you specifically: an approval, a question, a "
                        "choice.",
                        "This is the one to watch. It is not a stall.",
                    ],
                    [
                        "blocked",
                        "Something outside the conversation is in the way.",
                        "Read the pane. The sub-reason names what.",
                    ],
                    [
                        "idle",
                        "Nothing is happening in the terminal.",
                        "Not the same as finished. Background work can be running.",
                    ],
                ],
            ),
        ),
        (
            "note",
            "<b><code>idle</code> is never reported as finished on its own.</b> An agent waiting "
            "on you and an agent with background work still running render identically in a quiet "
            "terminal and mean the opposite, so nothing in swe-mux collapses them.",
        ),
        ("h2", "Where the reading comes from"),
        (
            "p",
            "Four independent sources, because no single one of them is reliable alone:",
        ),
        (
            "flat",
            [
                (
                    "Provider hooks",
                    "The structured events a CLI emits about its own turn. The strongest signal "
                    "where a harness publishes one.",
                ),
                (
                    "The transcript",
                    "What the conversation file on disk says happened, read as the branching "
                    "structure it actually is rather than as a flat log.",
                ),
                (
                    "The terminal itself",
                    "What is on the screen. The weakest signal, and deliberately never the one an "
                    "approval decision is made from.",
                ),
                (
                    "The CLI's own state files",
                    "Whatever the harness writes about itself outside the transcript.",
                ),
            ],
        ),
        (
            "p",
            "Every transition is kept in a durable ledger, so a status that went wrong can be "
            "investigated afterwards. A watchdog exists for the case a session gets stuck "
            "reporting one state.",
        ),
        ("h2", "Approvals"),
        (
            "p",
            "When a harness asks permission, the decision is made from <b>its structured "
            "permission request</b>, never from what is on the terminal screen. There is a floor "
            "in the code that is checked before any configured mode, so no setting can reach past "
            "it.",
        ),
        (
            "note",
            "<b>swe-mux never decides to deny.</b> That is deliberately not a decision it makes; "
            "a denial stays yours. Approval modes are per-conversation and live in Settings, "
            "Prompt queue, Approvals.",
        ),
        ("h2", "Why a status is not a done signal"),
        (
            "p",
            "The prompt queue does not wait for a binary done. It waits for a readiness gate and a "
            "stability window, because a state that just changed and a state that has settled are "
            "different facts and only the second one is safe to act on. "
            '<a href="../queue/">The prompt queue</a> covers what that means when you turn '
            "automatic delivery on.",
        ),
        ("h2", "When status looks wrong"),
        (
            "p",
            "<code>mux doctor</code> carries a fleet status-health check, and "
            "<code>mux doctor --export</code> includes the status timeline's sink statistics. A "
            "status that is wrong for one session is a bug worth reporting with that export "
            "attached; a status that is wrong for every session usually means a harness updated "
            "underneath swe-mux and moved something it reads.",
        ),
    ],
)

CONTROL_PLANE = Page(
    slug="control-plane",
    title="What the control plane is",
    description=(
        "The layer that decides which session you look at next: deterministic facts, model-free "
        "detectors, attention ranking with an interrupt budget. Off by default, per Project."
    ),
    lede=(
        "The workbench is everything you look at. The <b>control plane</b> is the part that decides "
        "<em>what</em> you look at. It is off by default, per Project, and nothing in it can type, "
        "approve, or spawn."
    ),
    blocks=[
        ("h2", "Where the facts come from"),
        (
            "p",
            "Every other account of what an agent did is the transcript, which is the agent's own "
            "report of itself. swe-mux owns the pseudoterminal, so it can record what actually "
            "happened at the boundary where it happened: content hashes computed when a tool "
            "writes rather than by reading the file back afterwards, parsed test outcomes, git "
            "tree hashes, write-then-read lineage. Everything above reads that substrate.",
        ),
        ("h2", "The layers, cheapest first"),
        (
            "flat",
            [
                (
                    "Deterministic facts",
                    "Captured at the tool boundary. No model, no cost per session, and no way for "
                    "it to be wrong about whether a file was written.",
                ),
                (
                    "Model-free detectors",
                    "Loops and stalls, claims declared but not verified, documentation debt, and "
                    "provenance gaps. They read the facts and produce findings. No model runs, so "
                    "they cost nothing per session and cannot hallucinate a finding.",
                ),
                (
                    "Attention ranking",
                    "Ranked items with <b>a hard daily interrupt budget, four a day by "
                    "default</b>. Incidents merge rather than repeat, demotion rules are mined "
                    "from what you have dismissed and expire on their own, and the count of what "
                    "was suppressed is always shown rather than hidden.",
                ),
                (
                    "Model-backed observers",
                    "The only layer that spends money. Each one watches a run and reports, each is "
                    "gated by a per-Project opt-in <em>and</em> an install-wide ceiling, and each "
                    "carries its own budget.",
                ),
            ],
        ),
        ("h2", "The refusal that makes the rest safe"),
        (
            "p",
            "<b>An observer can never type, approve, spawn, execute a script, or change a "
            "Project.</b> That is structural rather than a policy setting - there is no code path "
            "from an observer to an action, so there is no configuration that would grant one.",
        ),
        (
            "p",
            "The consequence is that the control plane cannot rescue a stuck agent, and is not "
            "trying to. It tells you which one to go and look at.",
        ),
        ("h2", "The inverse arrow: what agents read back"),
        (
            "p",
            "A per-session MCP server lets an agent <em>pull</em> from the control plane rather "
            "than an orchestrator pushing commands down to it: sibling session status, prior "
            "resolutions, dead ends already hit, commit provenance, Project notes, the scan "
            "timeline.",
        ),
        (
            "p",
            "Its writes are bounded to four things, and each one ends at a human by default: "
            "staging a message into another session's queue, drafting a spawn request for someone "
            "to approve, arming a watch on a session it is waiting for, and interrupting or "
            "ending a session behind a per-Project grant. It starts nothing.",
        ),
        ("h2", "Turning it on"),
        (
            "steps",
            [
                "Open the <b>Automation dashboard</b> and its <b>Policy</b> tab. That is the one "
                "editor that can turn an automation off in either scope, which is what keeps every "
                "other gate additive-only.",
                "Enable the install-wide ceiling for the automations you want available at all.",
                "Enable them per Project, on the same matrix. A Project with no opt-in runs "
                "nothing regardless of the ceiling.",
                "Set budgets under the Policy tab's <b>Limits and budgets</b> disclosure. Settings, "
                "Automation shows status and links here rather than holding a second copy of the "
                "controls.",
            ],
        ),
        (
            "note",
            'The deterministic half - facts, detectors, provenance - has no model cost and is '
            'worth having on well before the model-backed half. '
            '<a href="../automation/">Automation and alerts</a> covers the observers, and '
            '<a href="../git/">Git</a> covers provenance.',
        ),
        ("h2", "What it deliberately does not do"),
        (
            "ul",
            [
                "It never actuates. No typing, no approving, no spawning, no killing.",
                "It never kills a process on suspicion. Hung processes are surfaced, not "
                "terminated.",
                "It sends nothing to a service this project operates, because there is no such "
                "service. Model-backed observers call the endpoint you configured, with your key.",
                "It fails closed. An observer that cannot run produces no finding rather than a "
                "guess.",
            ],
        ),
    ],
)

# ------------------------------------------------------------------ working in it

WORKSPACE = Page(
    slug="workspace",
    title="Panes, tabs, and the drawer",
    description=(
        "The workspace is a mixed tree of panes, tabs, and splits, with a utility drawer beside "
        "it. What each drawer tab acts on, and how the phone projects the same tree."
    ),
    lede=(
        "The workspace is a mixed tree of panes, tabs, and splits, with a utility drawer along the "
        "right edge. Everything in it acts on one of three things: the focused session, the active "
        "Project, or the whole application."
    ),
    blocks=[
        ("h2", "Panes and splits"),
        (
            "table",
            (
                ["Chord", "Action"],
                [
                    ["Ctrl+Alt+H", "Split the focused pane right"],
                    ["Ctrl+Alt+V", "Split the focused pane below"],
                    ["Ctrl+Alt+Z", "Zoom the focused pane, and unzoom it"],
                    ["Ctrl+Alt+D", "Detach the focused pane"],
                    ["Ctrl+Alt+Left / Right", "Focus the previous or next pane"],
                    ["Ctrl+Tab / Ctrl+Shift+Tab", "Focus the next or previous workspace tab"],
                ],
            ),
        ),
        (
            "p",
            "Split geometry is durable per-Project state, so a layout you built survives a reload "
            "and a restart. Panes can be dragged and dropped, stacked into tabs, and dissolved "
            "back out.",
        ),
        (
            "p",
            "A pane is not only a terminal. Notes, files, previews, and static documents all open "
            "into one.",
        ),
        ("h2", "The utility drawer"),
        (
            "p",
            "On the desktop it is an in-flow split beside the workspace with an always-visible "
            "launcher rail, so it never covers what you are working on. On a phone it is an "
            "overlay entering from the right edge. Same tabs, same order.",
        ),
        (
            "table",
            (
                ["Tab", "Acts on", "What it is for"],
                [
                    [
                        "Actions",
                        "the focused session",
                        "Rail keys, prompt templates, discovered agent skills, and the clipboard "
                        "history. Everything that puts text into the terminal.",
                    ],
                    [
                        "Queue",
                        "the focused session",
                        "Stage ordered messages against a mid-turn conversation, and decide when "
                        "they go.",
                    ],
                    [
                        "Transcript",
                        "the focused session",
                        "The conversation, read from the harness's own transcript.",
                    ],
                    [
                        "Activity",
                        "the focused session",
                        "What the run did: the scan timeline, the detectors' findings, and the "
                        "change map of files it actually wrote.",
                    ],
                    [
                        "Agent",
                        "the focused session",
                        "What this agent is running with: its tools, its policies, and its "
                        "instruction files.",
                    ],
                    [
                        "Files",
                        "the Project",
                        "A bounded file tree with editors. Picking a file opens it in a pane.",
                    ],
                    [
                        "Notes",
                        "the Project",
                        "The note collection and the editor itself, so you can read or add to a "
                        "note without losing the terminal on a phone.",
                    ],
                    [
                        "Git",
                        "the Project",
                        "Status, comparison, diff review, the worktree map, provenance, and the "
                        "landing strip.",
                    ],
                    [
                        "Processes",
                        "the focused session",
                        "What this session started, and what each one is serving. Hidden by "
                        "default.",
                    ],
                    [
                        "Schedule",
                        "the Project",
                        "What this Project will start later, with its own fleet-wide scope.",
                    ],
                    [
                        "Alerts",
                        "the application",
                        "The ranked attention inbox across every Project. The one application-wide "
                        "fleet view with a permanent tab.",
                    ],
                ],
            ),
        ),
        (
            "note",
            "Tabs can be hidden, reordered, and drawn as icons or titles in Settings, Appearance. "
            "A surface folded into another tab keeps its own command-palette entry and its own "
            "voice phrase, so nothing becomes reachable only by remembering where it went.",
        ),
        ("h2", "Watch here, act there"),
        (
            "p",
            "Two pairs of surfaces look like duplicates. "
            "The Queue <em>tab</em> is beside the terminal because deciding whether to send "
            "a message is a judgement about the agent's live state, and that state is only legible "
            "in the terminal; the <b>Fleet Queue</b> is a modal because it has no send button and "
            "nothing in it needs a terminal beside it. The Processes tab answers \"what is "
            "<em>this</em> session running\" beside the pane; the Resources dialog covers the "
            "terminal and answers the fleet-wide version.",
        ),
        ("h2", "On a phone"),
        (
            "p",
            "The phone renders a single-pane projection of the same workspace tree rather than a "
            "second layout, so a session you split on the desktop is still there and still in "
            "order. Navigation is a top bar with horizontal swipe between Projects, an overlay "
            "for the sidebar, and the drawer from the right edge.",
        ),
        (
            "p",
            "A touch key rail carries the keys a phone keyboard does not have, and a two-finger "
            "swipe down enters a read and select mode that keeps the on-screen keyboard out of "
            "the way.",
        ),
        ("h2", "The command palette reaches everything"),
        (
            "p",
            "<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd>. Every command in the application is in "
            "it, including the ones with no default chord, and every one of them is bindable to a "
            'chord or a mobile gesture. <a href="../keyboard/">The keyboard reference</a> has the '
            "defaults.",
        ),
    ],
)

QUEUE = Page(
    slug="queue",
    title="The prompt queue",
    description=(
        "Stage ordered messages against a session that is mid-turn. Durable, head-of-line, and "
        "with automatic delivery off by default. What turning it on actually means."
    ),
    lede=(
        "The prompt queue is how you get work to an agent that is busy without typing into a "
        "terminal that is not listening. It is durable, head-of-line, and bound to the "
        "<em>conversation</em> rather than to the pane, so it survives a pane closing and a "
        "session being resumed."
    ),
    blocks=[
        ("h2", "Staging a message"),
        (
            "p",
            "Open the <b>Queue</b> tab in the utility drawer while a session is mid-turn and "
            "write your next message. It sits there. Nothing is sent until either you send it or "
            "an automatic-delivery rule you turned on decides to.",
        ),
        (
            "p",
            "Head-of-line means the queue delivers in order and one at a time: the second message "
            "does not overtake the first because the first is waiting on something.",
        ),
        ("h2", "Automatic delivery is off by default"),
        (
            "p",
            "A queued message waits for you. If you queue three messages and expect them to "
            "flow, nothing will happen. That is the design, and it is the default most likely to "
            "catch you out.",
        ),
        (
            "p",
            "When you do turn it on, it is per conversation, and it does not wait for a binary "
            "done signal, because no such signal exists that is reliable across vendors. It waits "
            "for a <b>readiness gate</b> and then a <b>stability window</b>: the session has to be "
            "ready, and it has to have stayed ready. A state that just changed and a state that "
            "has settled are different facts, and only the second is safe to act on.",
        ),
        ("h2", "The controls, and what each one is for"),
        (
            "flat",
            [
                (
                    "Per-conversation default and override",
                    "Automatic delivery is decided per conversation, so one long-running worker "
                    "can flow while everything else waits.",
                ),
                (
                    "Quiet hours",
                    "A window where nothing is delivered automatically, for the case where you "
                    "want the agent to have work waiting when you wake up rather than to have "
                    "burned through it at 3am.",
                ),
                (
                    "Emergency pause",
                    "One switch that stops all automatic delivery immediately, across every "
                    "conversation.",
                ),
                (
                    "Per-item expiry",
                    "A message can be given a deadline after which it is dropped rather than "
                    "delivered late into a context it no longer fits.",
                ),
                (
                    "Consecutive-send cap",
                    "A limit on how many messages can be delivered in a row without evidence that "
                    "the agent replied to any of them, so a wedged session cannot be fed the whole "
                    "queue.",
                ),
            ],
        ),
        (
            "note",
            "All of these are in Settings, Prompt queue, under <b>Auto-delivery</b>. The shipped "
            "defaults are the conservative ones.",
        ),
        ("h2", "Agent-to-agent messages"),
        (
            "p",
            "One session can put a message into another session's queue. The floor is stated in "
            "one sentence: <b>a non-human sender's write ends at a human.</b> An unsolicited "
            "message from an agent is staged for you to approve, not delivered.",
        ),
        (
            "p",
            "There are exactly two ways past that floor, and both are the <em>receiver's</em> "
            "authorization rather than the sender's claim. The receiving session can hold a "
            "standing grant to accept agent messages, or the message can answer a request that "
            "receiving session itself made. Answering a question somebody asked is narrower than "
            "the floor rather than an erosion of it, and the narrowing stays exactly as wide as "
            "the request: the requester alone, a fixed template, the run that asked, a cap, and "
            "it ends with the authority that accepted the request.",
        ),
        (
            "note",
            "Arming is still not delivery. A message that is allowed to be armed still goes "
            "through the same readiness gate and stability window as one you armed yourself.",
        ),
        ("h2", "The Fleet Queue"),
        (
            "p",
            "The application-wide view of who wrote what to whom: every staged message, its "
            "sender, and its state. It is a modal rather than a drawer tab, because it has no send "
            "button - it is where you watch, and the Queue tab is where you act.",
        ),
        ("h2", "Seeding a new session"),
        (
            "p",
            "A message can be staged against a session that does not exist yet. Spawning binds it "
            "on the first run, which is how a scheduled run or a spawn request arrives with its "
            "instructions already attached rather than needing a second act to deliver them.",
        ),
        ("h2", "Scheduled runs"),
        (
            "p",
            "Cron, interval, or one-off. A schedule is a <b>deferred press of a button you could "
            "have pressed yourself</b>, so it goes through the ordinary spawn path, the ordinary "
            "resume path, and the ordinary prompt queue, and grows no second authority anywhere.",
        ),
        (
            "note",
            "Definitions stay on the machine rather than in the repository, because a schedule "
            "committed to a repository would arm itself in every clone and every worktree. The "
            "Schedule drawer tab is where they live.",
        ),
    ],
)

GIT = Page(
    slug="git",
    title="Git, worktrees, and landing",
    description=(
        "Status and diff review beside the terminal, a worktree map, commit-level provenance "
        "split into committer and contributor, and a land queue that fast-forwards only."
    ),
    lede=(
        "The Git drawer tab reads the repository behind the Project: what changed, who changed it, "
        "which worktrees exist, and - when you turn it on - a queue that lands a finished branch "
        "behind a verification gate."
    ),
    blocks=[
        ("h2", "What it reads, and how often"),
        (
            "p",
            "Attached sessions are polled for branch, HEAD, dirty count, upstream divergence, "
            "worktree identity, and lines changed. Every session is swept once a minute regardless "
            "of whether a pane is open, because a cached observation that only refreshes while you "
            "are looking at it goes stale exactly when you stop looking.",
        ),
        (
            "note",
            "Line counts cover <b>tracked</b> changes only. An untracked file raises the dirty "
            "count and contributes no lines, because there is nothing to compare it against. A "
            "count that could not be measured is reported as unmeasured rather than as zero - a "
            "display that conflates the two reports a clean tree for a repository it merely failed "
            "to read.",
        ),
        (
            "p",
            "An agent pane reports its working directory through the harness's hooks rather than "
            "through the shell's prompt, because a CLI holding the terminal draws no prompt. That "
            "is what makes an agent working inside a worktree report <em>that</em> worktree's "
            "branch and diff rather than the primary checkout's.",
        ),
        ("h2", "The worktree map"),
        (
            "p",
            "One row per worktree, with its files, its changes, and the live sessions standing in "
            "it. Creating a worktree is here; so is removing one.",
        ),
        (
            "p",
            "Removal is <b>declined rather than forced</b> wherever Git itself would refuse: a "
            "locked worktree, one carrying submodules, one that is unclean. A removal renames the "
            "checkout out of the way and purges it in the background, so a large tree does not "
            "hold the request open, and the registration is dropped with a targeted removal "
            "rather than a global prune - a prune would take every other checkout whose directory "
            "merely happens to be missing.",
        ),
        ("h2", "Commit provenance"),
        (
            "p",
            "Which session and which conversation produced a commit, with a confidence, split into "
            "two roles that answer different questions:",
        ),
        (
            "flat",
            [
                (
                    "Committer",
                    "The session that ran the commit. After a batch landing this is often the one "
                    "that did the integrating rather than the work.",
                ),
                (
                    "Contributor",
                    "The session whose evidence shows it authored the content. This is the one you "
                    "want when you are asking who wrote a line.",
                ),
            ],
        ),
        (
            "p",
            "It is drawn from deterministic capture at the tool boundary rather than from the "
            "agent's account of its own work, and it changes nothing in the repository - no "
            "trailers, no notes, no rewritten history.",
        ),
        ("h2", "The land queue"),
        (
            "p",
            "Landing a finished worktree branch is three steps, and the queue runs them one branch "
            "at a time so two agents cannot land into the same trunk at once.",
        ),
        (
            "steps",
            [
                "<b>Reconcile.</b> Merge the current trunk into the branch, inside the worktree "
                "that owns it.",
                "<b>Verify.</b> Run the verification command <em>whose exact bytes you "
                "approved</em>. Editing the command and approving it are two separate acts through "
                "two separate routes, which is what stops an agent approving the command its own "
                "land will run.",
                "<b>Fast-forward.</b> Move the trunk, and only forward. Git refuses a "
                "fast-forward on divergence and refuses one that would overwrite local changes, "
                "which is what makes this step safe for a machine to take: the pipeline cannot "
                "lose work by construction.",
            ],
        ),
        (
            "p",
            "It never resolves a conflict and it never runs a gate nobody approved. A conflict or "
            "a failed gate both need judgement and both belong to the branch's own agent, so they "
            "come back to that session as a bounded message rather than being worked around here.",
        ),
        ("h2", "Two ways the gate is skipped, both recorded"),
        (
            "flat",
            [
                (
                    "A documentation-only change",
                    "After reconciling, the incoming paths are matched against a <b>closed</b> "
                    "allowlist. Matching a fixed list is a total function with no model and no "
                    "heuristic in it, so it is not a judgement call. Anything it cannot answer "
                    "with certainty - a source file, a rename, a submodule, an unreadable diff - "
                    "runs the full gate.",
                ),
                (
                    "A verdict already earned",
                    "A verify-only request runs every earlier step identically, so its verdict is "
                    "the verdict a land would have produced. That verdict is kept against the git "
                    "<em>tree</em> it ran over and the digest of the command that ran, so a later "
                    "land over identical content reuses it. A moved trunk yields a new tree and "
                    "the gate runs again.",
                ),
            ],
        ),
        (
            "note",
            "Both are written into the request's event trail with the reason, and the skipped step "
            "is still present in the trail. A documentation-only land would otherwise read exactly "
            "like one that passed three minutes of tests.",
        ),
        ("h2", "What a running gate is allowed to tell you"),
        (
            "p",
            "Every signal is observed or absent, never estimated. A step number counts markers the "
            "gate itself printed. A step <em>total</em> exists only where a byte-identical run has "
            "already passed, and is withdrawn the moment a run overruns it. No percentage is "
            "derived at either end, because this repository's own gate has steps that take 45 "
            "seconds and 3 seconds - a proportion drawn over that would be fiction, and a wrong "
            "number gets acted on where an absent one does not.",
        ),
        ("h2", "Turning it on"),
        (
            "p",
            "The land queue is off by default, per Project. It is enabled in Settings, Projects, "
            "and its queue, its verification command, and its grants live in a compact strip at "
            "the head of the Git tab's Map. A blocked row opens that strip rather than drawing a "
            "second copy of the controls.",
        ),
        (
            "note",
            "The manual two commands remain the fallback and the thing to reach for when the queue "
            "is not enabled: reconcile in the worktree, then fast-forward from the primary "
            "checkout.",
        ),
        ("h2", "First-time repository initialization"),
        (
            "p",
            "A Project pointed at a folder that is not a repository yet can be initialized from "
            "the Git tab. It is an explicit act; swe-mux does not create repositories under you.",
        ),
    ],
)

NOTES_FILES = Page(
    slug="notes-files",
    title="Notes, files, and previews",
    description=(
        "Project-owned notes in a real Markdown editor, a bounded file tree with editors, leased "
        "watches, and dev-server previews proxied so a phone never needs a raw port."
    ),
    lede=(
        "Three Project-scoped surfaces that open into panes rather than living only in the drawer: "
        "the note collection, the file tree, and whatever your sessions are serving."
    ),
    blocks=[
        ("h2", "Notes"),
        (
            "p",
            "A flat, Project-owned collection in a real Markdown editor - rendered headings, "
            "nested lists, checkboxes, an outline you can jump through. Notes are created from the "
            "Notes drawer, not from a terminal or a session, because a note belongs to the Project "
            "rather than to whichever session happened to be focused.",
        ),
        (
            "table",
            (
                ["Chord", "Action"],
                [
                    ["Ctrl+Alt+N", "Open the current Project's notes"],
                    ["Ctrl+Alt+J", "Jump to a heading in the focused note"],
                    ["Ctrl+F", "Find inside the focused note, from within the editor"],
                ],
            ),
        ),
        (
            "p",
            "There is one global note, the <b>Scratchpad</b>, pinned at the top of the drawer in "
            "every scope. It lives outside every Project and every repository, it cannot be "
            "renamed or deleted, and it is the right place for something that is not about the "
            "repository you happen to be standing in.",
        ),
        (
            "note",
            "<b>Deleting a note is recoverable.</b> The file moves into a trash directory inside "
            "the Project's own ignored state rather than being unlinked, so the bytes are still on "
            "disk when you want them. The one refusal is emptying a Project's collection: the last "
            "note cannot be deleted, and the control says why rather than merely being disabled.",
        ),
        (
            "p",
            "The whole note tree is Project-local application state and is git-ignored by a "
            "manifest swe-mux writes at Project creation. An ignore file you already wrote is "
            "yours and is never rewritten.",
        ),
        ("h2", "Files"),
        (
            "p",
            "A bounded file tree over the Project root with editors, ignore patterns, and a reveal "
            "into the host file manager. Picking a file opens it into a pane, so reading code "
            "beside a running agent does not cost you the terminal.",
        ),
        (
            "p",
            "Text edits are <b>revision-checked</b>: an edit carries the revision it was made "
            "against and is refused if the file moved underneath it. That is the difference "
            "between a conflict you are told about and a change you silently lost.",
        ),
        (
            "p",
            "<b>Watches are leased.</b> A watch on a directory is non-recursive and expires unless "
            "it is renewed, which is what stops a browser tab left open for a week holding "
            "filesystem watches on a repository nobody is looking at.",
        ),
        ("h2", "Previews"),
        (
            "p",
            "swe-mux notices what your sessions started and what each one is listening on. A local "
            "development server can be opened as a <b>Preview</b> tab, and it is proxied through "
            "the daemon's own URL, hot module reload included - so a phone reaches it without ever "
            "needing a raw port on the host, and without a second thing to expose on your tailnet.",
        ),
        (
            "p",
            "A static document in the checkout can be previewed the same way, under a sandbox "
            "policy, which is how you read a built HTML report without leaving the workspace.",
        ),
        (
            "note",
            "<b>swe-mux finds hung processes; it does not kill them.</b> A suspected orphan is "
            "surfaced with what it is and what started it. Terminating it stays an explicit act "
            "you take.",
        ),
        ("h2", "Prompt templates"),
        (
            "p",
            "Saved reusable messages, in the Actions tab. Selecting one <b>inserts and never "
            "submits</b>: a template is text, not an action, and a template that sent itself "
            "would be a macro with no confirmation step.",
        ),
        ("h2", "The clipboard history"),
        (
            "p",
            "A ring of recent captures, in the Actions tab beside the templates, because both do "
            "the same thing - put text into the focused agent. On a phone this is often the only "
            "practical way to move a long string into a terminal.",
        ),
    ],
)

HISTORY = Page(
    slug="history",
    title="History and transcripts",
    description=(
        "One search and one resume across every supported harness, read from the vendors' own "
        "transcript files, which are never moved, rewritten, or deleted."
    ),
    lede=(
        "History is one search and one resume across every supported harness. It reads the native "
        "transcript directories each vendor's CLI already writes, and <b>never moves, rewrites, or "
        "deletes them</b>."
    ),
    blocks=[
        ("h2", "What it gives you"),
        (
            "ul",
            [
                "<b>One search</b> over every conversation, in every Project, from every harness, "
                "rather than one search per vendor.",
                "<b>One resume.</b> Picking a past conversation restarts it in the harness it "
                "belongs to, through the same path a scheduled resume uses.",
                "<b>Cross-vendor review.</b> Reading what a Codex run did and what a Claude run "
                "did without learning two transcript formats.",
                "<b>Project scope.</b> A search runs across everything by default, or inside one "
                "Project.",
            ],
        ),
        ("h2", "Transcripts are read, never owned"),
        (
            "p",
            "Native transcript directories are reconciled at startup: swe-mux indexes what is "
            "there, and the files stay exactly where the vendor's CLI put them. Uninstalling "
            "swe-mux leaves every conversation intact, because none of them were ever its to hold.",
        ),
        ("h2", "A transcript is a graph, not a log"),
        (
            "p",
            "A Claude transcript is an append-only branching structure rather than a flat list. A "
            "retry, a rewind, or an edited message leaves the abandoned branch in the file. "
            "swe-mux linearizes it, which means two things worth knowing:",
        ),
        (
            "ul",
            [
                "The indexing projection <b>drops the branches the conversation left</b>, so a "
                "search does not return three variants of the same exchange.",
                "The human reader <b>marks them</b> rather than hiding them, so you can see that a "
                "retry happened.",
            ],
        ),
        (
            "note",
            "Neither of them reconstructs the live branch by walking the parent chain, because a "
            "parallel tool batch parents each result to its own call - a chain walk would drop "
            "every result but the last, and would do it silently.",
        ),
        ("h2", "The Transcript tab"),
        (
            "p",
            "The drawer's Transcript tab is the live version of the same reading, pinned to the "
            "focused session. It is what you read when you want to know what the agent actually "
            "said rather than what is currently on screen, and it survives the terminal being "
            "cleared.",
        ),
        ("h2", "When two entries share a conversation"),
        (
            "p",
            "A conversation that was resumed under two different session identities can end up as "
            "two history rows. That is reported and repairable rather than left to accumulate:",
        ),
        (
            "code",
            "mux history-duplicates            # report what would change\n"
            "mux history-duplicates repair     # merge each conversation's rows",
        ),
        ("h2", "Usage is reconstructed from these"),
        (
            "p",
            "Agent spend is not metered by swe-mux, because a subscription CLI does not report a "
            "price per call. It is <em>reconstructed</em> from the transcripts, which is why the "
            'usage view labels it as an estimate. <a href="../usage/">Accounts, usage, and '
            "budgets</a> explains the three separate pots and why they are never summed.",
        ),
    ],
)

VOICE = Page(
    slug="voice",
    title="Voice and the assistant",
    description=(
        "Read aloud in three policy layers, hands-free conversation with local transcription and "
        "wake words, and an assistant whose confirmation floor is not configurable."
    ),
    lede=(
        "Two independent halves. <b>Read aloud</b> speaks what an agent said. <b>Talk</b> listens, "
        "and turns speech into navigation, commands, dictation, or an assistant turn. Neither one "
        "needs the other, and both are off until you turn them on."
    ),
    blocks=[
        ("h2", "Read aloud, in three ordered layers"),
        (
            "p",
            "The policy is three explicit layers rather than one switch:",
        ),
        (
            "steps",
            [
                "<b>The master switch.</b> Off means nothing is generated <em>or</em> played, "
                "anywhere, by any path. Settings, Voice owns it.",
                "<b>Per session.</b> Does <em>this</em> session generate speech at all.",
                "<b>Per device, plus one global rule.</b> The focused session plays here; every "
                "other session <b>holds</b> its clip and shows it as held on that pane's strip "
                "rather than talking over you.",
            ],
        ),
        (
            "p",
            "A reply can be spoken as a <b>summary</b>, which costs a call to the cheap model you "
            "configured, or <b>verbatim</b>, which costs nothing and calls no model at all.",
        ),
        ("h2", "Which voice engine speaks"),
        (
            "table",
            (
                ["Engine", "Where it runs", "What it costs"],
                [
                    [
                        "The operating system's voice",
                        "On your machine, through the OS speech engine.",
                        "Nothing. The default, and the fallback.",
                    ],
                    [
                        "A local neural model",
                        "On your machine, from the voice-local extra. The Windows desktop bundle "
                        "always carries it.",
                        "Nothing per call. A one-time model download.",
                    ],
                    [
                        "External Edge TTS",
                        "A Microsoft endpoint, and text leaves the machine.",
                        "Explicitly experimental. Requires a versioned acknowledgement before "
                        "anything is sent, and is never bundled.",
                    ],
                ],
            ),
        ),
        (
            "note",
            "A word the local model can only spell out letter by letter is fixable with a "
            "respelling in Settings, Voice, applied without a restart. Each spell-out is recorded, "
            "so the list of words that need one is a list rather than something you have to "
            "notice.",
        ),
        ("h2", "Talk: hands-free conversation"),
        (
            "p",
            "Browser microphone capture, on-device voice activity detection, and local "
            "transcription. Nothing is written to disk, and the audio is decoded from memory.",
        ),
        (
            "flat",
            [
                (
                    "Wake words and commands",
                    "Both are yours to configure. The set of <em>actions</em> a phrase can map to "
                    "is fixed - send, append, cancel, undo, mute, read, interrupt, help, standby, "
                    "resume, hold, proceed, stop and a few more - and the phrases that reach them "
                    "are not.",
                ),
                (
                    "Spoken navigation",
                    "Projects and sessions are addressed by number, following the order the "
                    "sidebar draws them, so \"Project 2, Session 3\" resolves both coordinates at "
                    "once without needing a list read out first.",
                ),
                (
                    "Standby and stop",
                    "<em>Standby</em> keeps the microphone on and ignores everything except a "
                    "resume or a stop. <em>Stop</em> releases the microphone.",
                ),
                (
                    "Hold and proceed",
                    "The brainstorm pair. Plain speech buffers instead of becoming turns until "
                    "\"go ahead\" releases it as one consolidated turn.",
                ),
                (
                    "Push to talk",
                    "Hold <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Space</kbd> for capture with no "
                    "endpoint detection at all.",
                ),
            ],
        ),
        (
            "p",
            "Playback leaves capture open, so you can interrupt. Speech immediately ducks the "
            "audio, waits for the echo to drain, and stops the stream if what it heard was really "
            "you - and restores playback if it was the echo.",
        ),
        (
            "note",
            "<b>The microphone needs a secure context.</b> On a phone that means HTTPS, which is "
            'what the Tailscale Serve step on the <a href="../phone/">phone page</a> is for.',
        ),
        ("h2", "The Mux assistant"),
        (
            "p",
            "Ask for something in words and have it happen. The division of labour is the whole "
            "design: <b>the model proposes names, deterministic code resolves and executes them "
            "through the paths a button would have used.</b> The model does not get a shell, and "
            "it does not get an API of its own.",
        ),
        (
            "p",
            "<b>The confirmation floor for a consequential action is not configurable.</b> There "
            "is no setting that turns it off, because a setting that turns off confirmation is the "
            "setting somebody turns off once and then forgets about.",
        ),
        (
            "p",
            "A turn has a round budget it is told about and asked to work within, and running out "
            "of rounds is announced rather than silent. Anything you say while a turn is running "
            "is queued and merged into the next fragment rather than refused, because a refusal "
            "had nowhere to go and simply lost what you said.",
        ),
        ("h2", "Where the routing goes"),
        (
            "p",
            "Three tiers, in order, and the assistant is the last resort rather than the first: a "
            "deterministic grammar for help, fleet queries, navigation, and reply reading; then a "
            "conservative fuzzy pass; then - only on no match, and only when it is enabled - the "
            "assistant.",
        ),
    ],
)

AUTOMATION = Page(
    slug="automation",
    title="Automation and alerts",
    description=(
        "Model-backed observers that watch a run and report, an attention inbox with a hard daily "
        "interrupt budget, and push notifications to the device you are actually at."
    ),
    lede=(
        "Automation is the model-backed half of the control plane: observers that watch a run and "
        "report on it. It produces exactly two things, an <b>attention item</b> or a <b>run "
        "note</b>, and it can never do anything else."
    ),
    blocks=[
        ("h2", "What an observer can and cannot do"),
        (
            "p",
            "It can read a run and write a finding. It <b>can never type, approve, spawn, execute "
            "a script, or change a Project</b>. That is structural: there is no code path from an "
            "observer to an action, so there is no setting that would grant one and no "
            "misconfiguration that could produce one.",
        ),
        ("h2", "Two outputs, and where each one lives"),
        (
            "flat",
            [
                (
                    "A run note",
                    "Something worth recording that is not worth interrupting you for. It has "
                    "exactly one home: the Activity tab's Findings.",
                ),
                (
                    "An attention item",
                    "Something that may need you. It appears in the Alerts drawer tab and in the "
                    "Automation dashboard's Activity tab - the same component over the same data "
                    "rather than a second copy of it.",
                ),
            ],
        ),
        ("h2", "The interrupt budget"),
        (
            "p",
            "Items are <b>ranked</b>, and there is a <b>hard daily interrupt budget, four a day "
            "by default</b>, across four channels split by how expensive the item is to resolve.",
        ),
        (
            "ul",
            [
                "<b>Incidents merge rather than repeat.</b> The same problem recurring is one item "
                "with a count, not fifteen.",
                "<b>Demotion rules are mined and expire.</b> What you dismiss teaches it what not "
                "to raise, and those rules time out rather than accumulating into a filter nobody "
                "remembers writing.",
                "<b>The suppressed count is always shown.</b> You are told how many items did not "
                "make the budget, and why each one did not.",
            ],
        ),
        ("h2", "The Automation dashboard"),
        (
            "table",
            (
                ["Tab", "What it holds"],
                [
                    [
                        "Policy",
                        "The one editor that can turn an automation <em>off</em> in either scope: "
                        "the install-wide ceiling and every per-Project opt-in, as one matrix. "
                        "Limits and budgets live behind a disclosure here.",
                    ],
                    [
                        "Usage",
                        "What automation has spent, by automation and by Project. The same "
                        "component the Usage view draws, over the same numbers.",
                    ],
                    [
                        "Activity",
                        "The attention inbox and the run notes, at fleet scope.",
                    ],
                ],
            ),
        ),
        (
            "note",
            "Settings, Automation shows status and links here rather than holding a second set of "
            "switches. Two controls writing one key is how a setting ends up with two answers.",
        ),
        ("h2", "Budgets"),
        (
            "p",
            "Every automation carries a cap, and a cap is tokens, dollars, or both. The reason it "
            "is both is a real limitation rather than a preference: <b>a dollar cap cannot bind "
            "against a provider that reports no cost.</b> Absent cost is recorded as unmeasured "
            "rather than as zero, every total drawn over it reads as a floor, and the token axis "
            "is the honest backstop.",
        ),
        (
            "note",
            "Rate limits and per-call ceilings are deliberately <em>not</em> budgets. They count "
            "acts and bound one request; a budget bounds a period's spend. Conflating them makes "
            "one of the two silently unenforced.",
        ),
        ("h2", "Alerts and push"),
        (
            "p",
            "Web push to a phone, with per-device preferences, plus sounds on the desktop. Which "
            "device you are actually at is decided <b>once, for the whole application</b>, from a "
            "presence heartbeat, rather than each feature guessing separately and disagreeing.",
        ),
        (
            "p",
            "Push goes through your browser vendor's push service, which is how web push works "
            "everywhere, and only after you subscribe a device. Nothing is sent to a service this "
            "project operates, because there is not one.",
        ),
        ("h2", "The deterministic detectors need none of this"),
        (
            "p",
            "Loops and stalls, declared-but-not-verified claims, documentation debt, and "
            "provenance gaps all run with <b>no model at all</b>. They cost nothing per session "
            "and cannot hallucinate a finding, and they are worth having on well before any "
            'observer is. <a href="../control-plane/">The control plane</a> covers the layering.',
        ),
    ],
)

USAGE = Page(
    slug="usage",
    title="Accounts, usage, and budgets",
    description=(
        "Switching between provider accounts you own, the three spend pots that are never summed, "
        "where a model call goes, and what a cap can and cannot enforce."
    ),
    lede=(
        "Three separate things live near each other here and are constantly confused: the accounts "
        "your agent CLIs log in with, what has been spent, and the caps that bound future spend."
    ),
    blocks=[
        ("h2", "Provider accounts"),
        (
            "p",
            "Save, relabel, reauthenticate, switch, and remove Claude and Codex logins, with "
            "subscription-window polling so you can see how much of a window is left. Only "
            "authentication is copied, and switching is always an explicit act you take.",
        ),
        (
            "note",
            "<b>This is for one person switching between accounts they personally own and pay "
            "for</b>, replacing the logout and login cycle the provider CLIs otherwise require. It "
            "is not account pooling and not a way around a usage limit: accounts are never shared "
            "between people, credentials stay local and go nowhere but the provider's own "
            "endpoints, and sessions are never load-balanced across accounts.",
        ),
        (
            "code",
            "mux accounts             # what is saved, and which is active\n"
            "mux accounts verify      # re-verify each saved identity\n"
            "mux accounts audit       # the credential audit trail",
        ),
        ("h2", "The three pots, and why they are never summed"),
        (
            "table",
            (
                ["Pot", "What it is", "How exact it is"],
                [
                    [
                        "Agent spend",
                        "What your agent CLIs used, on your subscription.",
                        "An estimate. It is reconstructed from transcripts, because a "
                        "subscription CLI reports no price per call.",
                    ],
                    [
                        "Automation spend",
                        "What swe-mux's own model-backed features used, on your metered key.",
                        "Metered, billed by the call, and exact where the provider reports a cost.",
                    ],
                    [
                        "Provider quota",
                        "How much of a provider's rolling window is left.",
                        "Not money at all. A share of a window.",
                    ],
                ],
            ),
        ),
        (
            "p",
            "<b>No surface computes a total across them</b>, and every figure carries the basis it "
            "was drawn on. A number without its basis is the bug, not a missing feature: adding "
            "an estimate to a metered charge and calling it a total produces a figure that is "
            "wrong in a way nobody can see.",
        ),
        (
            "note",
            "Agent spend itself has <em>two</em> denominators - one over every transcript on the "
            "machine, one over only the runs swe-mux observed - so the subset is labelled by its "
            "denominator everywhere it appears and is never presented as the agent total.",
        ),
        ("h2", "Where a model call goes"),
        (
            "p",
            "Every model setting is edited in <b>one place</b>: Settings, Accounts, Models. Each "
            "feature tab keeps a read-only row naming the model it resolved to, with a link back "
            "to that editor.",
        ),
        (
            "p",
            "The reason they are together rather than beside the features they configure is that "
            "<b>changing endpoint is the operation that touches all of them at once</b>. Split "
            "across four tabs, that operation meant four tabs of hunting to find out whether "
            "anything had broken, with no screen anywhere answering \"what does this install call, "
            "and does the endpoint I just switched to even have it\".",
        ),
        (
            "note",
            "A blank value is not always legal, and the two cases must never look the same. An "
            "<b>override</b> left blank falls through to the cheap model. A <b>pin</b> left blank "
            "is refused by the control.",
        ),
        ("h2", "Budgets"),
        (
            "p",
            "A cap is <code>{tokens, dollars, mode}</code>, edited through one control, per "
            "feature. Both axes exist because a dollar cap cannot bind against a provider that "
            "reports no cost - absent cost is unknown rather than zero, so the ledger records it "
            "as unmeasured, totals drawn over it read as a floor, and the token axis is the honest "
            "backstop.",
        ),
        ("h2", "What reaches the network, and what does not"),
        (
            "p",
            "Your agent CLIs contact their own vendors under your own subscription. swe-mux "
            "proxies nothing and resells nothing, and it is not in the execution path between the "
            'CLI and its vendor. <a href="../data/">Where data lives</a> lists every request '
            "swe-mux itself can make.",
        ),
    ],
)

# --------------------------------------------------------------------- reference

SETTINGS = Page(
    slug="settings",
    title="Settings reference",
    description=(
        "Every Settings tab, what it owns, and the rule that decides whether a switch is global, "
        "per Project, or per device."
    ),
    lede=(
        "Settings is seventeen tabs in four groups. <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>S</kbd> "
        "opens it, and the sidebar is the only navigation: the longer tabs are several pages "
        "each rather than one scrolling document."
    ),
    blocks=[
        ("h2", "Workspace"),
        (
            "table",
            (
                ["Tab", "What it owns"],
                [
                    [
                        "General",
                        "Application-wide behaviour that does not belong to a subsystem below.",
                    ],
                    [
                        "Projects",
                        "The Project registry, and <b>most per-Project switches</b>: the control "
                        "plane opt-ins, the land queue, and the authority fields that decide what "
                        "an agent may do in this repository.",
                    ],
                    [
                        "Terminals",
                        "The PTY supervisor, session recovery and its checkpoints, multi-device "
                        "input, and terminal behaviour.",
                    ],
                    ["Git", "Polling cadence, comparison refs, and worktree defaults."],
                    ["Processes", "Process discovery, preview ownership, and preview lifetime."],
                ],
            ),
        ),
        ("h2", "Agents"),
        (
            "table",
            (
                ["Tab", "What it owns"],
                [
                    [
                        "Harnesses",
                        "Each harness, its detection state, the executable it resolved to, an "
                        "override for that path, and launch profiles.",
                    ],
                    [
                        "Accounts",
                        "Three pages: <b>Provider accounts</b> (saved logins and switching), "
                        "<b>Model provider</b> (the endpoint and key), and <b>Models</b> - the "
                        "single place every model setting in the application is edited.",
                    ],
                    [
                        "Prompt queue",
                        "Six pages: Overview, <b>Auto-delivery</b>, <b>Approvals</b>, "
                        "<b>Agent messaging</b>, <b>Agent actuation</b>, and Queue history.",
                    ],
                    [
                        "Automation",
                        "Status only, and a link to the Automation dashboard's Policy tab, which "
                        "is the one editor that can turn an automation off.",
                    ],
                    ["Usage", "Spend views and the budget caps."],
                ],
            ),
        ),
        ("h2", "Interface"),
        (
            "table",
            (
                ["Tab", "What it owns"],
                [
                    [
                        "Appearance",
                        "Theme, density, which drawer tabs are drawn and in what order, and "
                        "whether the rail shows icons or titles.",
                    ],
                    [
                        "Input",
                        "Five pages: Pointer, <b>Mobile terminal</b>, <b>Clipboard history</b>, "
                        "<b>Touch gestures</b>, and <b>Keyboard shortcuts</b>.",
                    ],
                    ["Text editor", "The note and Markdown editor's own behaviour."],
                    [
                        "Voice",
                        "Five pages: <b>Read aloud</b>, <b>Talk and dictation</b>, <b>Voice "
                        "commands</b>, <b>Mux assistant</b>, and Diagnostics.",
                    ],
                ],
            ),
        ),
        ("h2", "System"),
        (
            "table",
            (
                ["Tab", "What it owns"],
                [
                    ["Alerts", "Notification preferences, per device, plus sounds."],
                    [
                        "Remote",
                        "The listeners, the Tailscale integration, and Serve. This is where the "
                        "access boundary is configured, and the unsupported bindings are refused "
                        "rather than merely warned about.",
                    ],
                    [
                        "Diagnostics",
                        "Health, logs, the software-update check, and the frozen-app rebuild on "
                        "Windows.",
                    ],
                ],
            ),
        ),
        ("h2", "Which scope a setting has, and why"),
        (
            "flat",
            [
                (
                    "Global",
                    "Written to <code>config.toml</code> in the data directory. This is the "
                    "daemon's own configuration, and it is what a second device sees too.",
                ),
                (
                    "Per Project",
                    "Anything that decides how much swe-mux <em>does</em>: the control plane, "
                    "automation, the scan timeline, the code graph, the land queue, and the "
                    "authority fields. The point is that most of your Projects are not the one you "
                    "want a scan budget spent on.",
                ),
                (
                    "Per device",
                    "Written by the browser, in <code>settings.json</code>. Autoplay, gestures, "
                    "notification preferences, the theme - the things that are properties of the "
                    "screen you are at rather than of the install.",
                ),
            ],
        ),
        ("h2", "Two rules the panel is built on"),
        (
            "ul",
            [
                "<b>Two controls never write one key.</b> Where a value is shown in a second "
                "place, it is read-only and links to its editor. That is why every feature tab "
                "names its model without offering a picker.",
                "<b>A gate can only turn something on.</b> A surface that needs a switch enabled "
                "can enable it in place; turning it off is always the owning editor's. That "
                "asymmetry is what makes a write reachable from a drawer pane safe.",
            ],
        ),
        ("h2", "Editing the file directly"),
        (
            "p",
            "<code>config.toml</code> in the data directory is the same configuration, and it is "
            "schema-versioned with a pre-migration backup written beside it. Settings is the "
            "better route because it validates. A config that does not validate is one of the "
            "four common causes of a daemon that will not start, and it is the first check "
            "<code>mux doctor</code> reports on.",
        ),
    ],
)

CLI = Page(
    slug="cli",
    title="Command line reference",
    description=(
        "Every mux subcommand, every muxd flag, the exit-code contract scripts branch on, and how "
        "the CLI resolves which daemon to talk to."
    ),
    lede=(
        "Three commands are installed: <code>mux</code> talks to a running daemon, <code>muxd</code> "
        "<em>is</em> the daemon, and <code>swe-mux</code> is the Windows desktop window and tray."
    ),
    blocks=[
        ("h2", "muxd, the daemon"),
        (
            "table",
            (
                ["Flag", "What it does"],
                [
                    ["--host", "Override the configured listener host. Must be a loopback address."],
                    ["--port", "Override the configured port for this run."],
                    ["--config", "Use a specific configuration file."],
                    ["--dev", "Development mode."],
                    [
                        "--local-only",
                        "Do not open the direct Tailscale listener for this run. Useful when you "
                        "are isolating a network problem, because it removes tailnet detection "
                        "from the startup path.",
                    ],
                    [
                        "--where",
                        "Print where swe-mux is installed, whether its commands are on PATH, and "
                        "how to run it when they are not. Then exit.",
                    ],
                    [
                        "--shutdown",
                        "Reap every session the PTY supervisor owns, stop the supervisor, and "
                        "exit. <b>This ends live sessions.</b> It is the deliberate stop-everything "
                        "command, never part of an update.",
                    ],
                ],
            ),
        ),
        (
            "note",
            "<code>--host</code> takes a loopback address only, because the Tailscale listener is "
            "<em>detected</em> rather than configured. Binding a LAN interface is an unsupported "
            "configuration rather than a flag that is missing.",
        ),
        ("h2", "mux, the client"),
        (
            "table",
            (
                ["Command", "What it does"],
                [
                    ["mux ls", "List sessions. Filter with --project, --state, --backend."],
                    [
                        "mux spawn",
                        "Spawn a session. --project is required; --backend, --name, --profile, "
                        "--exe, and repeatable --arg shape it.",
                    ],
                    [
                        "mux send",
                        "Send input to a session, named by id, name, or a unique id prefix.",
                    ],
                    ["mux kill", "Terminate a session."],
                    ["mux resume", "Resume a history entry. --project is required."],
                    ["mux projects", "List Projects."],
                    ["mux profiles", "List launch profiles."],
                    [
                        "mux harnesses",
                        "List every harness in the registry with its detection state. The first "
                        "thing to run when a CLI you have installed is not being recognised.",
                    ],
                    ["mux history", "List conversation history."],
                    [
                        "mux history-duplicates",
                        "Report, or with <code>repair</code> merge, history entries that share one "
                        "conversation.",
                    ],
                    [
                        "mux accounts",
                        "<code>list</code>, <code>verify</code>, or <code>audit</code> saved "
                        "provider accounts.",
                    ],
                    [
                        "mux doctor",
                        "The consolidated read-only diagnostics report. <code>--export</code> "
                        "prints the full bundle as JSON.",
                    ],
                    [
                        "mux update",
                        "Report the release check and this install's update path. "
                        "<code>--install VERSION</code> downloads that release and verifies its "
                        "hash against the published manifest before anything is staged.",
                    ],
                    [
                        "mux reload-daemon",
                        "Restart the daemon in place. Sessions survive when the PTY supervisor is "
                        "on; <code>--force</code> restarts without it and <b>kills every "
                        "session</b>.",
                    ],
                    [
                        "mux install-shortcut",
                        "Create the Start Menu and Desktop shortcuts a wheel install structurally "
                        "cannot (Windows). <code>--startup</code> adds a run-at-login entry, "
                        "<code>--remove</code> takes them all away. Idempotent.",
                    ],
                ],
            ),
        ),
        (
            "p",
            "Every subcommand takes <code>--json</code> to print the raw daemon response instead "
            "of a table, and <code>--url</code> to point at a specific daemon.",
        ),
        ("h2", "Which daemon mux talks to"),
        (
            "p",
            "Resolved in this order, and the order is the contract: <code>--url</code>, then the "
            "<code>MUX_URL</code> environment variable, then the configured host and port, then "
            "<code>http://127.0.0.1:8765</code>.",
        ),
        ("h2", "Exit codes"),
        (
            "table",
            (
                ["Code", "Meaning"],
                [
                    ["0", "Success. For doctor, a full report with no failing check."],
                    ["1", "A doctor report with a failing check, local or full."],
                    ["2", "Usage error."],
                    [
                        "3",
                        "Daemon unreachable. For doctor, also a clean local report.",
                    ],
                    ["4", "Daemon HTTP error."],
                    ["5", "Ambiguous session name."],
                    ["6", "Not found."],
                ],
            ),
        ),
        (
            "note",
            "<b>A degraded report never exits 0</b>, so a script gating on <code>mux doctor</code> "
            "keeps working whether or not a daemon is running. The doctor codes compose the two "
            "that already existed rather than adding a scheme of their own.",
        ),
        ("h2", "mux doctor has two modes, and picks between them itself"),
        (
            "flat",
            [
                (
                    "Full report",
                    "When a daemon answers. Categories for the daemon, each harness, remote "
                    "access, the firewall, host prerequisites, fleet status health, the background "
                    "loops, conversation freshness, WSL, and optional assets. Nothing in it reads "
                    "a secret, a terminal byte, or message content.",
                ),
                (
                    "Local report",
                    "When nothing answers, because a daemon that will not start is the most likely "
                    "new-user failure and the full report presupposes exactly the thing that is "
                    "missing. It checks the interpreter, the imports, the config, the frontend "
                    "bundle, the data directory, the database, the port, this host's "
                    "pseudoterminal backend, and the supervisor bundle.",
                ),
            ],
        ),
        (
            "note",
            "The local report marks what it did <em>not</em> check as <code>unchecked</code> "
            "rather than folding it into a pass. Folding a skipped check into <em>ok</em> claims "
            "health nobody measured, which is worse than the connection error it replaced.",
        ),
    ],
)

KEYBOARD = Page(
    slug="keyboard",
    title="Keyboard and the command palette",
    description=(
        "Every default chord, the command palette that reaches the rest, and how rebinding and "
        "mobile gestures work."
    ),
    lede=(
        "Two entry points cover almost everything: "
        "<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> for a terminal at the Project root, and "
        "<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd> for the command palette, which reaches every "
        "command in the application including the ones with no default chord."
    ),
    blocks=[
        ("h2", "The defaults"),
        (
            "table",
            (
                ["Chord", "Command"],
                [
                    ["Ctrl+Alt+T", "New terminal in the current Project"],
                    ["Ctrl+Alt+O", "New terminal, custom"],
                    ["Ctrl+Alt+P", "Open the command palette"],
                    ["Ctrl+Alt+S", "Open Settings"],
                    ["Ctrl+Alt+N", "Open the current Project's notes"],
                    ["Ctrl+Alt+J", "Jump to a heading in the focused note"],
                    ["Ctrl+Alt+H", "Split the focused pane right"],
                    ["Ctrl+Alt+V", "Split the focused pane below"],
                    ["Ctrl+Alt+Z", "Toggle focused pane zoom"],
                    ["Ctrl+Alt+D", "Detach the focused pane"],
                    ["Ctrl+Alt+Right", "Focus the next pane"],
                    ["Ctrl+Alt+Left", "Focus the previous pane"],
                    ["Ctrl+Tab", "Focus the next workspace tab"],
                    ["Ctrl+Shift+Tab", "Focus the previous workspace tab"],
                    ["Ctrl+Shift+F", "Find in the focused terminal"],
                    ["Ctrl+Alt+1 .. 9", "Activate the first nine Projects, in sidebar order"],
                    ["Ctrl+Alt+Space (held)", "Push to talk, with no endpoint detection"],
                ],
            ),
        ),
        ("h2", "Commands with no default chord"),
        (
            "p",
            "Many commands ship unbound on purpose, because they already have a button in view and "
            "a chord for them would be a shortcut to something you can already see. They are all "
            "in the palette, and all of them are bindable. Among them: browsing history, opening "
            "the Projects registry, the global Scratchpad, the usage and quota views, the "
            "Automation dashboard, session rename and kill, broadcast input, pane stacking and tab "
            "movement, the sidebar filter, the application menu, and the pinned note outline.",
        ),
        ("h2", "Rebinding"),
        (
            "p",
            "Settings, Input, Keyboard shortcuts. A binding needs <kbd>Ctrl</kbd>, <kbd>Alt</kbd>, "
            "or <kbd>Meta</kbd> plus a non-modifier key; <kbd>Shift</kbd> alone is refused, "
            "because it shadows typing.",
        ),
        (
            "note",
            "Overrides are stored in <code>keybindings.json</code> in the data directory. A "
            "binding for a command that was later renamed is migrated forward rather than dropped, "
            "so a chord you set does not silently stop working after an upgrade.",
        ),
        ("h2", "What the terminal keeps"),
        (
            "p",
            "swe-mux deliberately does not intercept the chords a shell or a TUI needs. "
            "<kbd>Ctrl</kbd>+<kbd>C</kbd>, <kbd>Ctrl</kbd>+<kbd>D</kbd>, "
            "<kbd>Ctrl</kbd>+<kbd>R</kbd>, <kbd>Ctrl</kbd>+<kbd>L</kbd> and their neighbours reach "
            "the process, which is why the application's own chords are behind "
            "<kbd>Ctrl</kbd>+<kbd>Alt</kbd> rather than bare <kbd>Ctrl</kbd>.",
        ),
        ("h2", "Mobile gestures"),
        (
            "p",
            "The same command registry is bindable to touch gestures in Settings, Input, Touch "
            "gestures. Three defaults are worth knowing:",
        ),
        (
            "table",
            (
                ["Gesture", "Command"],
                [
                    ["Two-finger swipe up", "Open the current Project's notes"],
                    [
                        "Two-finger swipe down",
                        "Read and select mode, which also lowers a keyboard being held up outside "
                        "a terminal",
                    ],
                    ["Swipe up on the command rail", "Toggle the swe-mux menu"],
                    ["Horizontal swipe on the top bar", "Step through Projects, in sidebar order"],
                ],
            ),
        ),
        ("h2", "Voice reaches the same registry"),
        (
            "p",
            "Spoken commands resolve through voice aliases on the ordinary command registry rather "
            "than through a parallel list, which is what keeps a command reachable by chord, "
            "palette, gesture, and voice without four definitions of it. "
            '<a href="../voice/">Voice</a> covers what is deliberately excluded from the spoken '
            "set.",
        ),
    ],
)

DATA = Page(
    slug="data",
    title="Where data lives, and what leaves the machine",
    description=(
        "The data directory and what each part of it holds, exactly which network requests swe-mux "
        "makes, what is worth backing up, and what an uninstall leaves behind."
    ),
    lede=(
        "swe-mux runs on your machine and talks to no service this project operates. This page is "
        "the precise version of that sentence: where every file goes, and every request that can "
        "leave."
    ),
    blocks=[
        ("h2", "The data directory"),
        (
            "p",
            "Resolved in this order, and the order is the contract:",
        ),
        (
            "steps",
            [
                "<code>MUX_DATA_DIR</code>, if it is set and not empty.",
                "<code>~/.mux</code>, if it already exists - on <em>every</em> host. An existing "
                "directory always wins, so a machine that has one keeps its data rather than "
                "silently starting from an empty one beside it.",
                "Otherwise the platform convention: <code>~/.mux</code> on Windows, "
                "<code>~/Library/Application Support/swe-mux</code> on macOS, and "
                "<code>$XDG_DATA_HOME/swe-mux</code> (else "
                "<code>~/.local/share/swe-mux</code>) on Linux.",
            ],
        ),
        ("h2", "What is in it"),
        (
            "table",
            (
                ["Kind", "What it holds"],
                [
                    [
                        "Configuration",
                        "<code>config.toml</code> (schema-versioned, with a pre-migration "
                        "<code>.bak</code>), <code>settings.json</code> for per-device browser "
                        "settings, <code>keybindings.json</code>, and the automation rule files.",
                    ],
                    [
                        "Your content",
                        "<code>notes/</code> for the global Scratchpad, <code>prompts/</code> for "
                        "the prompt library, and <code>media/</code> for session attachments.",
                    ],
                    [
                        "Databases",
                        "<code>mux.db</code> holds history, telemetry, the status timeline, the "
                        "deterministic facts, the prompt queue, schedules, clipboard history, the "
                        "code graph, and the durable session registry. "
                        "<code>land-queue.sqlite3</code> holds the land queue.",
                    ],
                    [
                        "Credentials",
                        "Saved provider logins, the metered-API key store, and the web-push "
                        "identity. On Windows the key values are encrypted for the current user; "
                        "on macOS the Keychain and on Linux the secret service hold them instead.",
                    ],
                    [
                        "Logs",
                        "<code>daemon.log</code>, <code>access.log</code>, the supervisor's own "
                        "logs, and the lifecycle ledger. All of them rotate, so a noisy day cannot "
                        "grow an unbounded file.",
                    ],
                    [
                        "Regenerated per run",
                        "The agent launcher shims, per-harness hook and MCP registration files, "
                        "per-session identity, process-discovery files, and certificate material. "
                        "All safely deletable.",
                    ],
                    [
                        "Media and capture",
                        "Synthesized speech clips (bounded, 200 MB by default), downloaded speech "
                        "models, preview screenshots, and terminal recovery checkpoints (bounded "
                        "per session, per age, and per count).",
                    ],
                ],
            ),
        ),
        (
            "note",
            "<b>Your Projects' content is not in here and is not swe-mux's to hold.</b> A Project "
            "points at a directory you already own. <code>.swe-mux/</code> inside a checkout is "
            "per-machine state, not something the repository carries.",
        ),
        ("h2", "The one request swe-mux makes on its own behalf"),
        (
            "p",
            "A daily <code>GET</code> of <code>https://swemux.dev/version.json</code>, to find out "
            "whether a newer release exists. Nothing downloads. The file is byte-identical for "
            "every install, and the request carries <b>no query string, no custom header, no "
            "cookie, and no identifier</b> of the machine or the install.",
        ),
        (
            "p",
            "One switch turns it off, in Settings, Diagnostics, Software updates, and off means no "
            "request is made at all rather than a request that is discarded.",
        ),
        (
            "p",
            "Installing an update is a separate act you take: <code>mux update --install "
            "&lt;version&gt;</code> downloads that release, checks its SHA-256 against the "
            "published manifest before anything is staged, and refuses rather than installs if the "
            "release would need a new terminal supervisor - which would end your live sessions.",
        ),
        ("h2", "Everything else that can reach the network"),
        (
            "p",
            "Each of these is a feature you turn on, or your own agent CLI talking to its own "
            "vendor. None of them is on by default.",
        ),
        (
            "flat",
            [
                (
                    "Your agent CLIs",
                    "They contact their vendors under your subscription. swe-mux is not in that "
                    "path: it proxies nothing and resells nothing.",
                ),
                (
                    "Model-backed features",
                    "Summarization, the assistant, and some control-plane features call an "
                    "OpenRouter-compatible endpoint <b>with your own key</b>, and are off until "
                    "you configure one.",
                ),
                (
                    "Web push",
                    "Goes through your browser vendor's push service, which is how web push works, "
                    "and only after you subscribe a device.",
                ),
                (
                    "On-device speech models",
                    "Downloaded once from the model host, pinned by revision and verified by "
                    "SHA-256.",
                ),
                (
                    "Saved provider accounts",
                    "Poll that vendor's own usage endpoint, with the credential you saved.",
                ),
                (
                    "Experimental Edge TTS",
                    "Reaches a Microsoft endpoint, and requires an explicit versioned "
                    "acknowledgement before any text leaves the machine.",
                ),
            ],
        ),
        (
            "note",
            "There is no analytics, no crash reporting, and no account. There is no swe-mux server "
            "for an account to exist on.",
        ),
        ("h2", "Backing it up"),
        (
            "p",
            "Stop the daemon first, or accept that the database's write-ahead log files are part "
            "of the copy - a data directory copied while a daemon is running leaves a log the "
            "restoring host has to recover.",
        ),
        (
            "ul",
            [
                "<code>mux.db</code> and <code>land-queue.sqlite3</code>, each with their "
                "<code>-wal</code> and <code>-shm</code> siblings if present.",
                "<code>config.toml</code>, <code>settings.json</code>, "
                "<code>keybindings.json</code>, and the automation rule files.",
                "<code>prompts/</code>, <code>notes/</code>, <code>media/</code>.",
                "The provider accounts directory, the metered-key store, and the push identity. "
                "<b>These are credentials - treat the backup as a secret.</b>",
            ],
        ),
        (
            "p",
            "Every log and everything regenerated per run is safely disposable. So is the "
            "synthesized speech cache, the downloaded models, and the recovery checkpoints, all of "
            "which cost only re-download or re-capture.",
        ),
        ("h2", "What an uninstall leaves"),
        (
            "p",
            "Uninstalling the package removes the three commands and the code. It does not touch "
            "the data directory, and it does not touch your agent CLIs' transcripts - swe-mux only "
            "ever read those where the vendor wrote them. Removing the data directory is a "
            "separate, deliberate act.",
        ),
    ],
)

# -------------------------------------------------------------------------- help

TROUBLESHOOTING = Page(
    slug="troubleshooting",
    title="Troubleshooting",
    description=(
        "The things that actually go wrong: a blank page, nothing on PATH, a daemon that will not "
        "start, sessions that look lost, a phone that cannot use the microphone."
    ),
    lede=(
        "Start with <code>mux doctor</code>. It is read-only, it works whether or not a daemon is "
        "running, and every failing row carries a remedy line, so the next step is not a "
        "documentation hunt."
    ),
    blocks=[
        ("h2", "Nothing is on my PATH after installing"),
        (
            "p",
            "This is the ordinary outcome of <code>pip install</code>, whose "
            "<code>WARNING: The scripts ... are installed in '...' which is not on PATH</code> "
            "scrolls past unread. <code>pip</code> installs into whichever environment is "
            "currently active and puts nothing on PATH globally.",
        ),
        (
            "code",
            "# The daemon, needing no PATH setup at all: `python -m swe_mux` is exactly `muxd`.\n"
            "python -m swe_mux\n"
            "\n"
            "# Where the three executables actually went.\n"
            "python -c \"import sysconfig; print(sysconfig.get_path('scripts'))\"\n"
            "\n"
            "# Or ask swe-mux itself.\n"
            "muxd --where",
        ),
        (
            "note",
            "<code>uv tool install swe-mux</code> and <code>pipx install swe-mux</code> both put "
            "all three on PATH globally, and are the recommended forms for that reason.",
        ),
        ("h2", "There is no desktop shortcut or Start Menu entry"),
        (
            "p",
            "There never will be from a Python install. Wheels have no post-install hook and pip "
            "runs no install-time code, so this is structural rather than a step somebody forgot. "
            "On Windows, <code>mux install-shortcut</code> creates them afterwards, and "
            "<code>--remove</code> takes them away again.",
        ),
        ("h2", "The page loads but there is no interface"),
        (
            "p",
            "Not a bug. The frontend bundle is build output and is not carried in the repository, "
            "so a fresh clone, a fresh worktree, and a CI checkout all have none: the daemon "
            "answers the API perfectly and serves no interface.",
        ),
        (
            "code",
            "npm --prefix frontend ci\nnpm --prefix frontend run build",
        ),
        (
            "note",
            "<code>mux doctor</code> distinguishes the two cases that look identical from the "
            "browser. In a source checkout the missing bundle is a <em>warning</em> carrying "
            "exactly that command. In an installed copy it is a <em>failure</em>, because a wheel "
            "that shipped without an interface is a broken artifact, and reinstalling from a "
            "complete one is the fix.",
        ),
        ("h2", "The daemon will not start"),
        (
            "p",
            "Run <code>mux doctor</code>. With nothing listening it produces the local report, and "
            "the first <code>FAIL</code> is the one to fix. Four causes account for most of them:",
        ),
        (
            "flat",
            [
                (
                    "A config that does not validate",
                    "The config check fails with the real parse or validation error. Fix "
                    "<code>config.toml</code>, or move it aside - a removed config is rewritten "
                    "with defaults on the next start. This is the fault the CLI otherwise hides, "
                    "because a config failure makes every <code>mux</code> command fall back to "
                    "the loopback default and possibly point at the wrong daemon.",
                ),
                (
                    "The port is already held",
                    "The port check fails and names the owner-finding command for your host "
                    "(<code>netstat -ano | findstr :&lt;port&gt;</code> on Windows, "
                    "<code>ss -ltnp</code> on Linux). Stop the owner, or set a different port.",
                ),
                (
                    "A broken install",
                    "The import check fails with the real exception attached. On Windows the "
                    "specific risk is the one compiled dependency in the runtime closure, where a "
                    "wheel mismatch or a missing Visual C++ runtime surfaces as an import error.",
                ),
                (
                    "An unwritable or missing data directory",
                    "The check distinguishes \"exists and cannot be written\" from \"does not "
                    "exist and cannot be created\", and points at <code>MUX_DATA_DIR</code>.",
                ),
            ],
        ),
        (
            "note",
            "<b>A bound listener is not a ready daemon.</b> Health answers 503 with the startup "
            "phase still in flight until the runtime exists, so \"it is listening but everything "
            "503s\" is the daemon still starting rather than a fault. "
            "<code>muxd --local-only</code> takes tailnet detection out of the startup path when "
            "you are isolating a network problem.",
        ),
        ("h2", "My sessions look lost after a restart"),
        (
            "p",
            "Which mechanism applies decides what you can get back, and there are two:",
        ),
        (
            "flat",
            [
                (
                    "The PTY supervisor, and it ships off",
                    "With it on, terminals are held by a separate process and survive a daemon "
                    "restart, an app rebuild, and a redeploy. With it off, a restart reaps every "
                    "session - which is why the restart endpoint refuses outright unless it is "
                    "forced. Turn it on in Settings, Terminals.",
                ),
                (
                    "Cold recovery, which ships on",
                    "Independent of the supervisor, and it covers what the supervisor cannot: "
                    "sessions whose daemon and terminal owner both died. They come back as "
                    "visible, dead, resumable rows rather than vanishing.",
                ),
            ],
        ),
        (
            "p",
            "Check the state before concluding anything. <code>mux doctor</code> against a running "
            "daemon reports supervisor attachment, and <code>mux doctor --export</code> lists cold "
            "sessions with their reason and capture state.",
        ),
        ("h2", "swe-mux cannot see an agent CLI I have installed"),
        (
            "code",
            "mux harnesses      # every harness in the registry, and what it resolved to",
        ),
        (
            "p",
            "If it resolved to the wrong binary, or to none, set the path for that harness in "
            "Settings, Harnesses. That is the usual fix on a machine carrying several installs of "
            "the same CLI. A harness the registry has never heard of is a different situation and "
            "not a fault: it runs perfectly in a real terminal, it just gets no layer on top.",
        ),
        ("h2", "The microphone does not work on my phone"),
        (
            "p",
            "Browsers restrict the microphone and the clipboard outside a secure context, so this "
            "is almost always HTTPS rather than swe-mux. In order:",
        ),
        (
            "steps",
            [
                "Confirm a daemon is listening on the port your configuration actually names.",
                "Confirm <code>tailscale serve status</code> proxies port 443 to that port.",
                "Confirm the phone is reaching the <code>.ts.net</code> <em>hostname</em>. The "
                "certificate is bound to the hostname, so the raw <code>100.x</code> address "
                "cannot serve HTTPS at all.",
                "Confirm <b>Use Tailscale DNS</b> is on, and that Android's Private DNS is off or "
                "automatic.",
            ],
        ),
        ("h2", "A queued message never got sent"),
        (
            "p",
            "<b>Automatic delivery is off by default.</b> A staged message waits for you to send "
            "it. If you queued three and expected them to flow, that is the design rather than a "
            'fault. <a href="../queue/">The prompt queue</a> covers turning it on and what the '
            "readiness gate and stability window mean.",
        ),
        ("h2", "A session says awaiting and looks stuck"),
        (
            "p",
            "<em>Awaiting</em> means the agent is waiting on <em>you</em> - an approval, a "
            "question, a choice. It is not a stall. Read the pane.",
        ),
        (
            "p",
            "<em>Idle</em> is the one that is genuinely ambiguous, and swe-mux never reports it as "
            "finished on its own: an agent waiting on you and an agent with background work still "
            "running look identical in a quiet terminal and mean the opposite.",
        ),
        ("h2", "Getting a diagnostic somebody can read"),
        (
            "code",
            "mux doctor --export > diagnostics.json",
        ),
        (
            "p",
            "That is the full bundle as JSON: the sanitized configuration, remote-connection "
            "state, firewall status, network counters, fleet status health, the status timeline "
            "and recovery sink statistics, any cold sessions, and the tails of the daemon log. It "
            "is an artifact to attach to a bug report rather than a table to read.",
        ),
        (
            "note",
            "It is sanitized rather than raw, and the full report reads no secret, no terminal "
            "byte, and no message content. Read it before you attach it anyway.",
        ),
        ("h2", "Something is still wrong"),
        (
            "p",
            f'Bugs go to <a href="{REPO}/issues">the issue tracker</a>, with the export attached. '
            f'Feature requests go to <a href="{REPO}/discussions/categories/ideas">the Ideas '
            "discussions</a>, where a thumbs-up is a vote - and it is worth checking "
            '<a href="../../roadmap/#not-planned">deliberately not on the roadmap</a> first, '
            "because some things are answered with a reason rather than with silence.",
        ),
    ],
)

CONTRIBUTING = Page(
    slug="contributing",
    title="Developing swe-mux",
    description=(
        "Maintainer material: running from a checkout, the verification gate a change has to pass, "
        "the DCO sign-off, and the extra rules a dependency change carries."
    ),
    lede=(
        "<b>This page is maintainer material.</b> Everything else under <code>/docs/</code> is "
        "written for somebody using swe-mux; this one is for somebody changing it, and it is the "
        "only page that sends you into the repository."
    ),
    blocks=[
        ("h2", "Running from a checkout"),
        (
            "code",
            "git clone https://github.com/jatoran/swe-mux\n"
            "cd swe-mux\n"
            "uv sync --extra desktop\n"
            "npm --prefix frontend ci\n"
            "npm --prefix frontend run build   # a fresh clone serves no UI until this runs once\n"
            "uv run --extra desktop swe-mux",
        ),
        (
            "note",
            "The frontend bundle is git-ignored build output. A fresh clone serves a blank page "
            "rather than an error until that build has run once.",
        ),
        ("h2", "The verification gate"),
        (
            "p",
            "Run it before opening a pull request. It is the same set continuous integration "
            "runs.",
        ),
        (
            "code",
            "uv run pytest tests -q -n auto --dist loadgroup\n"
            "uv run ruff check src/swe_mux tests packaging\n"
            "uv run mypy\n"
            "npx tsc --noEmit          # in frontend/\n"
            "npm test                  # in frontend/",
        ),
        (
            "p",
            "The Python suite runs across the host's cores: about 45 seconds on a 16-core "
            "machine against 242 serial, plus about 17 for the four checks after it. Continuous "
            "integration mirrors the same set on Windows and adds Linux and macOS legs, the "
            "production frontend build, and the full browser renderer suite.",
        ),
        ("h2", "Contributions arrive under a DCO"),
        (
            "p",
            "<code>git commit -s</code>, not a contributor licence agreement. A CLA would let "
            "the project relicense your contribution later. A developer certificate of origin "
            "does not, and that is the intended trade.",
        ),
        ("h2", "A dependency change carries extra rules"),
        (
            "p",
            "The rule the licence gate exists to enforce is that <b>a dependency's declared "
            "licence does not describe what its wheel ships</b>. So the check has two halves that "
            "must both stay alive: one reads the resolved dependency closure's metadata and runs "
            "in the verification gate, and the other reads the built desktop bundle for payloads "
            "by artifact name. Neither substitutes for the other.",
        ),
        (
            "ul",
            [
                "GPL and AGPL may never enter.",
                "LGPL needs an allowlist entry <em>and</em> must ship as replaceable source "
                "inside the bundle.",
                "The third-party notices file and its data are both generated. Never hand-edit "
                "either; run the audit and commit what it writes.",
            ],
        ),
        ("h2", "Where the rest is"),
        (
            "p",
            "The repository's own root documents are the ones to read, and they are written for "
            "this audience:",
        ),
        (
            "flat",
            [
                (
                    "CONTRIBUTING.md",
                    f'<a href="{BLOB}/CONTRIBUTING.md">What a change has to satisfy</a>: the '
                    "sign-off, the gate, and the dependency rules in full.",
                ),
                (
                    "CLAUDE.md",
                    f'<a href="{BLOB}/CLAUDE.md">The working rules for this repository</a>, '
                    "including the reload flows that let you apply a change without ending live "
                    "sessions.",
                ),
                (
                    "SECURITY.md",
                    f'<a href="{BLOB}/SECURITY.md">Where a vulnerability report goes</a>, and the '
                    "trust boundary it is measured against.",
                ),
                (
                    "The design documents",
                    "The maintained design contract lives in the repository under "
                    "<code>.docs/</code>. It is written for whoever maintains a subsystem next: it "
                    "states invariants and the decisions already made, names incident dates, and "
                    "is not written to be read as documentation. That is exactly why the pages "
                    "you are reading exist beside it rather than linking into it.",
                ),
                (
                    "The site itself",
                    f'<a href="{BLOB}/site/README.md">site/README.md</a> is the design record for '
                    "swemux.dev, including how these pages are generated and the gates that keep "
                    "them honest.",
                ),
            ],
        ),
        ("h2", "Reporting rather than contributing"),
        (
            "p",
            f'<a href="{REPO}/issues">Issues</a> are for bugs. '
            f'<a href="{REPO}/discussions/categories/ideas">Ideas discussions</a> are for feature '
            "requests, where a thumbs-up is a vote and the most-voted open ideas are drawn on the "
            '<a href="../../roadmap/">roadmap</a>. Describe the problem rather than the fix.',
        ),
    ],
)


SECTIONS: list[Section] = [
    Section("Getting started", [INSTALL, FIRST_SESSION, PHONE, AGENT_SETUP]),
    Section("Concepts", [PROJECTS, SESSIONS, STATUS, CONTROL_PLANE]),
    Section(
        "Working in it",
        [WORKSPACE, QUEUE, GIT, NOTES_FILES, HISTORY, VOICE, AUTOMATION, USAGE],
    ),
    Section("Reference", [SETTINGS, CLI, KEYBOARD, DATA]),
    Section("Help", [TROUBLESHOOTING, CONTRIBUTING]),
]


def pages() -> list[Page]:
    """Every page, in sidebar order. This is also the prev/next chain."""
    return [page for section in SECTIONS for page in section.pages]
