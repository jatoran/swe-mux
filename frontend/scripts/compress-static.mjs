import { createReadStream, createWriteStream } from 'node:fs'
import { readdir, stat } from 'node:fs/promises'
import { extname, resolve } from 'node:path'
import { pipeline } from 'node:stream/promises'
import { createGzip, constants } from 'node:zlib'

const OUTPUT_DIR = resolve(import.meta.dirname, '../../src/swe_mux/static')
const MIN_BYTES = 1024
const COMPRESSIBLE = new Set(['.css', '.html', '.js', '.json', '.mjs', '.svg', '.wasm', '.webmanifest'])

export function shouldCompress(path, size) {
  return size >= MIN_BYTES && COMPRESSIBLE.has(extname(path).toLowerCase())
}

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) files.push(...await filesUnder(path))
    else if (entry.isFile()) files.push(path)
  }
  return files
}

export async function compressStatic(directory = OUTPUT_DIR) {
  let sourceBytes = 0
  let encodedBytes = 0
  let count = 0
  for (const path of await filesUnder(directory)) {
    const metadata = await stat(path)
    if (!shouldCompress(path, metadata.size)) continue
    await pipeline(
      createReadStream(path),
      createGzip({ level: constants.Z_BEST_COMPRESSION }),
      createWriteStream(`${path}.gz`),
    )
    const encoded = await stat(`${path}.gz`)
    sourceBytes += metadata.size
    encodedBytes += encoded.size
    count += 1
  }
  return { count, sourceBytes, encodedBytes }
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(import.meta.filename)) {
  const result = await compressStatic()
  const reduction = result.sourceBytes
    ? Math.round((1 - result.encodedBytes / result.sourceBytes) * 100)
    : 0
  console.log(`precompressed ${result.count} static assets: ${result.sourceBytes} -> ${result.encodedBytes} bytes (${reduction}% smaller)`)
}
