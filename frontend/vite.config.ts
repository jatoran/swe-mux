import { createHash } from 'node:crypto'
import { defineConfig, type Plugin } from 'vite'
import preact from '@preact/preset-vite'

export const UI_BUILD_META_NAME = 'ui-build'

export function uiBuildId(fileNames: Iterable<string>): string {
  const emitted = [...fileNames].filter(name => name !== 'index.html').sort()
  return createHash('sha256').update(emitted.join('\n')).digest('hex')
}

function uiBuildIdentity(): Plugin {
  return {
    name: 'swe-mux-ui-build-identity',
    transformIndexHtml: {
      order: 'post',
      handler(html, context) {
        // Vite's development server has no finished bundle and deliberately carries no
        // identity. Only a production build can be compared with the daemon's static tree.
        if (!context.bundle) return html
        return {
          html,
          tags: [{
            tag: 'meta',
            attrs: { name: UI_BUILD_META_NAME, content: uiBuildId(Object.keys(context.bundle)) },
            injectTo: 'head',
          }],
        }
      },
    },
  }
}

export default defineConfig({
  plugins: [preact(), uiBuildIdentity()],
  // Dev-server only, and load-bearing for the renderer suite. The Change Map's layout
  // runs in a module worker, so its dependency is discovered at *runtime* rather than
  // by the entry scan — and vite answers a newly discovered dependency with a full
  // page reload. That reload lands mid-test, wiping the page state a spec has already
  // set up, and reads as a random failure with no relation to the change under test.
  // The Continuity editor is excluded for the mirror-image reason: pre-bundling rewrites its
  // module URL into `node_modules/.vite/deps`, where the sibling `continuity_wasm_bg.wasm`
  // its loader resolves relative to `import.meta.url` does not exist. Dev then answers that
  // request with the SPA fallback HTML, and the engine dies with a bogus
  // "serve the .wasm as application/wasm" error - every note editor blank under `npm run dev`,
  // and no way to exercise one from the renderer suite. Served unbundled, the relative URL
  // resolves to the real file. Production builds emit the asset properly and are unaffected.
  optimizeDeps: {
    include: ['sigma', 'graphology', 'graphology-layout-forceatlas2'],
    exclude: ['@continuity-editor/editor'],
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8765', '/pty': { target: 'ws://127.0.0.1:8765', ws: true }, '/events': { target: 'ws://127.0.0.1:8765', ws: true } } },
  build: { outDir: '../src/swe_mux/static', emptyOutDir: true },
})
