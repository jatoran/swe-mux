/** Public video delivery only. This module records no request or visitor information. */
export const VIDEO_ROUTES = ['/img/*.mp4', '/img/*.webm']
export const isVideoPath = pathname => /^\/img\/[^/]+\.(mp4|webm)$/.test(pathname)

export async function serveVideo(request, assets) {
  const headers = new Headers(request.headers)
  headers.delete('Range')
  headers.delete('If-Range')
  headers.set('Accept-Encoding', 'identity')
  return videoResponse(request, await assets.fetch(new Request(request, { headers })))
}

/** Invalid/multipart headers are ignored; a valid range outside the representation is 416. */
export function parseRange(header, size) {
  const match = /^bytes=(\d*)-(\d*)$/i.exec(header?.trim() ?? '')
  if (!match || (!match[1] && !match[2])) return null
  if (!size) return { unsatisfiable: true }
  if (!match[1]) {
    const count = Number(match[2])
    return count > 0 ? { start: Math.max(0, size - count), end: size - 1 } : { unsatisfiable: true }
  }
  const start = Number(match[1])
  const end = match[2] ? Math.min(Number(match[2]), size - 1) : size - 1
  return start >= size || start > end ? { unsatisfiable: true } : { start, end }
}

function ifRangeMatches(request, response) {
  const value = request.headers.get('If-Range')
  if (!value) return true
  if (value.startsWith('"') || value.startsWith('W/')) {
    return !value.startsWith('W/') && value === response.headers.get('ETag')
  }
  const modified = Date.parse(response.headers.get('Last-Modified') ?? '')
  const requested = Date.parse(value)
  return Number.isFinite(modified) && Number.isFinite(requested) && modified === requested
}

/** Discard preceding chunks and stop reading at the range boundary, without buffering the file. */
function sliceStream(body, start, end) {
  const reader = body.getReader()
  let position = 0
  return new ReadableStream({
    async pull(controller) {
      try {
        while (position <= end) {
          const { value, done } = await reader.read()
          if (done) throw new Error('Video asset ended before its declared Content-Length')
          const from = Math.max(0, start - position)
          const to = Math.min(value.byteLength, end + 1 - position)
          position += value.byteLength
          if (to > from) controller.enqueue(value.subarray(from, to))
          if (position > end) {
            controller.close()
            await reader.cancel()
            return
          }
          if (to > from) return
        }
      } catch (error) {
        controller.error(error)
        await reader.cancel(error).catch(() => {})
      }
    },
    cancel(reason) { return reader.cancel(reason) },
  })
}

/** Add seekable HTTP delivery to an ordinary full asset response. */
export function videoResponse(request, asset) {
  if (asset.status !== 200 || !['GET', 'HEAD'].includes(request.method)) return asset
  const length = asset.headers.get('Content-Length')
  const size = Number(length)
  const encoding = asset.headers.get('Content-Encoding')
  if (!/^\d+$/.test(length ?? '') || !Number.isSafeInteger(size) || (encoding && encoding !== 'identity')) return asset
  const headers = new Headers(asset.headers)
  headers.set('Accept-Ranges', 'bytes')
  if (request.method === 'HEAD') {
    void asset.body?.cancel().catch(() => {})
    return new Response(null, { status: 200, headers })
  }
  const range = ifRangeMatches(request, asset) ? parseRange(request.headers.get('Range'), size) : null
  if (!range) return new Response(asset.body, { status: 200, headers })
  if (range.unsatisfiable) {
    void asset.body?.cancel().catch(() => {})
    headers.set('Content-Range', `bytes */${size}`)
    headers.set('Content-Length', '0')
    return new Response(null, { status: 416, headers })
  }
  if (!asset.body) return asset
  headers.set('Content-Range', `bytes ${range.start}-${range.end}/${size}`)
  headers.set('Content-Length', String(range.end - range.start + 1))
  let body = sliceStream(asset.body, range.start, range.end)
  // Workers derives Content-Length from the stream, not from a manually supplied header.
  // https://developers.cloudflare.com/workers/runtime-apis/streams/transformstream/#fixedlengthstream
  if (typeof globalThis.FixedLengthStream === 'function') {
    const fixed = new globalThis.FixedLengthStream(range.end - range.start + 1)
    void body.pipeTo(fixed.writable).catch(() => {}) // Pipe errors also error the readable side.
    body = fixed.readable
  }
  return new Response(body, { status: 206, headers })
}
