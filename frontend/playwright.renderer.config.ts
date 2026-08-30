import os from 'node:os'
import { defineConfig, devices } from 'playwright/test'

// The suite saturates cores (a vite dev server, Chromium, the runner's workers)
// while live agent sessions share this host, so the runner drops itself to
// below-normal priority as soon as the config loads — before the web server or
// any browser is spawned, so every child inherits it (Windows passes the
// priority class down only from a BelowNormal or Idle parent, which is the
// class chosen; POSIX children inherit niceness unconditionally). Workers
// re-load this config but arrive already lowered, which the getPriority check
// recognizes, so the line logs once per run. `MUX_KEEP_PRIORITY=1` opts out;
// failure to lower is logged and never fatal, because the suite's job is the
// tests and a CI runner has nothing to yield to anyway.
if (!process.env.MUX_KEEP_PRIORITY) {
  try {
    if (os.getPriority() < os.constants.priority.PRIORITY_BELOW_NORMAL) {
      os.setPriority(os.constants.priority.PRIORITY_BELOW_NORMAL)
      console.error(`renderer suite: priority below normal (pid ${process.pid})`)
    }
  } catch (error) {
    console.error(`renderer suite: priority unchanged (${String(error)})`)
  }
}

// The dev server the suite drives is a *port*, and a port is process-wide. Combined with
// `reuseExistingServer`, a checkout that finds 4174 already taken silently runs its whole
// suite against whatever other checkout is serving it — a green run that proved nothing
// about the code under test, and, when the two trees differ, unreadable failures about
// harness pages that "do not exist". Worktrees are how parallel work happens in this repo,
// so each one needs a port it can call its own: `RENDERER_PORT` is that. CI leaves it
// unset and keeps 4174.
const PORT = Number(process.env.RENDERER_PORT || 4174)
const ORIGIN = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './test/renderer',
  testMatch: '*.spec.ts',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: ORIGIN,
    headless: true,
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] },
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${PORT}`,
    url: `${ORIGIN}/renderer-harness.html`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
})
