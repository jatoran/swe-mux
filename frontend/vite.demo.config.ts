import { rename } from 'node:fs/promises'
import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import preact from '@preact/preset-vite'

/**
 * The marketing-site demo build: the real app compiled against the fake
 * in-page daemon (`src/demo/`), emitted into `site/demo/` where the GitHub
 * Pages deploy uploads it verbatim.
 *
 * `base` is the deploy subpath — every asset URL in the emitted HTML is
 * `/demo/assets/...`, which is where Pages serves them from. The app's own
 * `/api` paths never resolve against this (the fetch shim answers them), and
 * the one absolute URL that escapes the shims — the preview pane's
 * `/preview/<id>/` iframe — is satisfied by static pages committed at
 * `site/preview/`.
 */

/** Vite names the HTML output after its input (`demo.html`); the deploy wants
 *  `site/demo/index.html` so `/demo/` serves it. */
function renameEntryHtml(): Plugin {
  return {
    name: 'swe-mux-demo-entry-rename',
    // writeBundle rather than closeBundle: closeBundle also runs on a failed
    // build, and the rename's ENOENT then buries the error that mattered.
    async writeBundle() {
      const outDir = resolve(__dirname, '../site/demo')
      await rename(resolve(outDir, 'demo.html'), resolve(outDir, 'index.html'))
    },
  }
}

export default defineConfig({
  plugins: [preact(), renameEntryHtml()],
  base: '/demo/',
  // The bundled service worker / icons / manifest belong to the real app's PWA
  // shell, not to an embedded demo.
  publicDir: false,
  // Voice capture is off in the demo config and can never run, but its lazily
  // imported assets (the 11 MB ONNX runtime, the Silero model) are still
  // statically reachable and would be emitted into — and committed under —
  // `site/demo/`. Stub them out of this build entirely.
  resolve: {
    alias: [
      { find: /^onnxruntime-web$/, replacement: resolve(__dirname, 'src/demo/stubs/onnxruntime.ts') },
      { find: /^.*ort-wasm-simd-threaded\.(wasm|mjs)\?url$/, replacement: resolve(__dirname, 'src/demo/stubs/assetUrl.ts') },
      { find: /^.*silero_vad_v5\.onnx\?url$/, replacement: resolve(__dirname, 'src/demo/stubs/assetUrl.ts') },
      { find: /^.*smart-turn-v3\.2-cpu\.onnx\?url$/, replacement: resolve(__dirname, 'src/demo/stubs/assetUrl.ts') },
    ],
  },
  optimizeDeps: {
    exclude: ['@continuity-editor/editor'],
  },
  build: {
    outDir: '../site/demo',
    emptyOutDir: true,
    rollupOptions: { input: resolve(__dirname, 'demo.html') },
  },
})
