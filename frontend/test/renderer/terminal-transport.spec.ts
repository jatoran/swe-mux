import { expect, test, type Page } from 'playwright/test'
import { harnessReady } from './harnessReady'

/**
 * Where a real `TerminalPane` opens its socket, and where it goes when a socket closes
 * (`DAEMON_LOAD_ISOLATION.md` item 30, `src/terminalTransport.ts`).
 *
 * The unit suite owns the rule itself; what only a browser can show is the pane acting on
 * it - reading a close code off a real close, reconnecting at once rather than after the
 * backoff, and ending up attached to whichever process actually holds the session, with
 * that process's bytes on the screen.
 */

type SocketRecord = { url: string; kind: 'gateway' | 'daemon'; opened: boolean; closedWith: number | null }

declare global {
  interface Window {
    __sockets(): SocketRecord[]
    __setBehavior(kind: 'gateway' | 'daemon', behavior: string): void
    __setHealthTransport(value: object | null): void
    __closeLive(code: number): void
    __screen(): string
  }
}

const open = async (page: Page, query: string) => {
  await page.goto(`/terminal-transport-harness.html?${query}`)
  await harnessReady(page, '__sockets', '__screen')
}

const kinds = (page: Page) => page.evaluate(() => window.__sockets().map(item => item.kind))
const screen = (page: Page) => page.evaluate(() => window.__screen())

test('with no gateway reported the pane attaches to the daemon, as it always has', async ({ page }) => {
  await open(page, '')
  await expect.poll(() => screen(page)).toContain('served by the daemon via /pty/transport-session')
  expect(await kinds(page)).toEqual(['daemon'])
})

test('a reported gateway carries the terminal', async ({ page }) => {
  await open(page, 'report=gateway')
  await expect.poll(() => screen(page)).toContain('served by the gateway via /gateway/pty/transport-session')
  expect(await kinds(page)).toEqual(['gateway'])
})

test('a session the gateway does not hold falls back to the daemon at once', async ({ page }) => {
  await open(page, 'report=gateway&gateway=use-daemon')
  await expect.poll(() => screen(page)).toContain('served by the daemon')
  const sockets = await page.evaluate(() => window.__sockets())
  expect(sockets.map(item => [item.kind, item.closedWith])).toEqual([['gateway', 4010], ['daemon', null]])
})

test('a gateway that cannot be reached falls back without waiting out a handshake', async ({ page }) => {
  await open(page, 'report=gateway&gateway=unreachable')
  const started = Date.now()
  await expect.poll(() => screen(page)).toContain('served by the daemon')
  // The handshake watchdog is eight seconds; a refused connection must not cost that.
  expect(Date.now() - started).toBeLessThan(5_000)
  expect(await kinds(page)).toEqual(['gateway', 'daemon'])
})

test('a gateway that never answers is given up on at the handshake deadline', async ({ page }) => {
  // Nothing comes back at all - a firewall dropping the connection - so only the pane's
  // eight-second watchdog ends the attempt, and it has to count as the gateway failing
  // rather than as one more try at the same address.
  await open(page, 'report=gateway&gateway=stall')
  await expect.poll(() => screen(page), { timeout: 20_000 }).toContain('served by the daemon')
  expect(await kinds(page)).toEqual(['gateway', 'daemon'])
})

test('a restarting gateway sends the pane to the daemon, and the daemon sends it back', async ({ page }) => {
  await open(page, 'report=gateway')
  await expect.poll(() => screen(page)).toContain('served by the gateway')
  // The gateway is replaced: it tells its browsers to reconnect, and the daemon serves
  // the session meanwhile.
  await page.evaluate(() => window.__closeLive(4011))
  await expect.poll(async () => (await kinds(page)).at(-1)).toBe('daemon')
  await expect.poll(() => screen(page)).toContain('served by the daemon')
  // The new gateway is up and holds the session: the daemon says so, and the pane moves.
  await page.evaluate(() => window.__closeLive(4012))
  await expect.poll(async () => (await kinds(page)).at(-1)).toBe('gateway')
  const last = (await page.evaluate(() => window.__sockets())).at(-1)!
  expect(last.opened).toBe(true)
  expect(last.closedWith).toBeNull()
})

test("the daemon's 4012 is followed even before any report named the gateway", async ({ page }) => {
  // The page has never seen a gateway; the daemon says this session is the gateway's, and
  // the pane asks health where that is rather than bouncing off the daemon forever.
  await open(page, 'daemon=use-gateway')
  await page.evaluate(() => window.__setHealthTransport({ transport: 'gateway', state: 'running', port: 8766 }))
  await expect.poll(() => screen(page), { timeout: 15_000 }).toContain('served by the gateway')
  const sockets = await page.evaluate(() => window.__sockets())
  expect(sockets[0]).toMatchObject({ kind: 'daemon', closedWith: 4012 })
  expect(sockets.at(-1)).toMatchObject({ kind: 'gateway', opened: true, closedWith: null })
})
