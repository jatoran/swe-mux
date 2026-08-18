import { defineConfig, devices } from 'playwright/test'

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
