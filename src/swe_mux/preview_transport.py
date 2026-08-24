"""Serving a Preview through the daemon: URL rewriting, static files, proxying."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web

from . import (
    app_keys as keys,
)
from .http_support import apply_security_headers
from .network_usage import (
    metered_websocket,
)
from .project_files import (
    read_static_preview_file,
)

log = logging.getLogger(__name__)


PREVIEW_HTTP_CONCURRENCY = 32


PREVIEW_WS_CONCURRENCY = 16


PREVIEW_REQUEST_BYTES = 10 * 1024 * 1024


PREVIEW_RESPONSE_BYTES = 20 * 1024 * 1024


PREVIEW_WS_MESSAGE_BYTES = 4 * 1024 * 1024


PREVIEW_WS_IDLE_SECONDS = 30 * 60


PREVIEW_WS_LIFETIME_SECONDS = 12 * 60 * 60


_PROXY_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-language",
    "content-type",
    "etag",
    "expires",
    "last-modified",
}


_PROXY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _preview_runtime_bridge(prefix: str, project_routes: dict[str, str] | None = None) -> str:
    encoded = json.dumps(prefix)
    encoded_routes = json.dumps(project_routes or {}, separators=(",", ":"))
    return f"""<script>(function(){{
const prefix={encoded};
const projectRoutes={encoded_routes};
// A client-side router reads location.pathname directly and Location is not
// patchable, so the mount point cannot be hidden from it the way asset URLs are.
// Advertise it instead: an app passes this to its router's basename (React Router,
// vue-router, SvelteKit) and falls back to "/" when it is not inside a preview.
window.__MUX_PREVIEW_BASE__=prefix;
const canonicalOrigin=function(url){{
  let protocol=url.protocol;
  if(protocol==="ws:")protocol="http:";
  if(protocol==="wss:")protocol="https:";
  let hostname=url.hostname.toLowerCase();
  if(hostname==="localhost"||hostname==="0.0.0.0")hostname="127.0.0.1";
  if(hostname==="[::]"||hostname==="::")hostname="[::1]";
  if(hostname.includes(":" )&&!hostname.startsWith("["))hostname="["+hostname+"]";
  const defaultPort=(protocol==="http:"&&url.port==="80")||(protocol==="https:"&&url.port==="443");
  return protocol+"//"+hostname+(url.port&&!defaultPort?":"+url.port:"");
}};
const route=function(value){{
  try {{
    const url=new URL(String(value),location.href);
    const projectPrefix=projectRoutes[canonicalOrigin(url)];
    if(projectPrefix){{
      url.protocol=location.protocol==="https:"?(url.protocol.startsWith("ws")?"wss:":"https:"):(url.protocol.startsWith("ws")?"ws:":"http:");
      url.host=location.host;
      url.pathname=projectPrefix+url.pathname.replace(/^\\/+/,"");
    }} else if(url.host===location.host&&!url.pathname.startsWith("/preview/")){{
      url.pathname=prefix+url.pathname.replace(/^\\/+/,"");
    }}
    return url.toString();
  }} catch (_) {{ return value; }}
}};
const urlAttributes=new Set(["src","href","action"]);
const routeAttribute=function(value){{
  const raw=String(value);
  if(raw.startsWith("/")&&!raw.startsWith("//"))return route(raw);
  try {{
    const url=new URL(raw,location.href);
    if(projectRoutes[canonicalOrigin(url)])return route(raw);
  }} catch (_) {{}}
  return value;
}};
const rewriteMarkup=function(value){{
  const source=String(value);
  return source.replace(/(\\b(?:src|href|action)\\s*=\\s*["'])([^"']+)/gi,
    function(_,start,target){{return start+routeAttribute(target);}});
}};
const nativeSetAttribute=Element.prototype.setAttribute;
Element.prototype.setAttribute=function(name,value){{
  const next=urlAttributes.has(String(name).toLowerCase())?routeAttribute(value):value;
  return nativeSetAttribute.call(this,name,next);
}};
const patchMarkupProperty=function(name){{
  const descriptor=Object.getOwnPropertyDescriptor(Element.prototype,name);
  if(!descriptor||typeof descriptor.set!=="function")return;
  try {{
    Object.defineProperty(Element.prototype,name,{{
      configurable:descriptor.configurable,
      enumerable:descriptor.enumerable,
      get:descriptor.get,
      set:function(value){{descriptor.set.call(this,rewriteMarkup(value));}}
    }});
  }} catch (_) {{}}
}};
patchMarkupProperty("innerHTML");
patchMarkupProperty("outerHTML");
const nativeInsertAdjacentHTML=Element.prototype.insertAdjacentHTML;
Element.prototype.insertAdjacentHTML=function(position,value){{
  return nativeInsertAdjacentHTML.call(this,position,rewriteMarkup(value));
}};
const patchUrlProperty=function(constructorName,name){{
  const constructor=window[constructorName];
  if(!constructor)return;
  const descriptor=Object.getOwnPropertyDescriptor(constructor.prototype,name);
  if(!descriptor||typeof descriptor.set!=="function")return;
  try {{
    Object.defineProperty(constructor.prototype,name,{{
      configurable:descriptor.configurable,
      enumerable:descriptor.enumerable,
      get:descriptor.get,
      set:function(value){{descriptor.set.call(this,routeAttribute(value));}}
    }});
  }} catch (_) {{}}
}};
[
  ["HTMLImageElement","src"],
  ["HTMLScriptElement","src"],
  ["HTMLIFrameElement","src"],
  ["HTMLSourceElement","src"],
  ["HTMLMediaElement","src"],
  ["HTMLLinkElement","href"],
  ["HTMLAnchorElement","href"],
  ["HTMLAreaElement","href"],
  ["HTMLFormElement","action"]
].forEach(function(entry){{patchUrlProperty(entry[0],entry[1]);}});
const rerouteOwnAttributes=function(element){{
  urlAttributes.forEach(function(name){{
    if(!element.hasAttribute(name))return;
    const current=element.getAttribute(name);
    const next=routeAttribute(current);
    if(next!==current)nativeSetAttribute.call(element,name,next);
  }});
}};
const rerouteTree=function(node){{
  if(!(node instanceof Element))return;
  rerouteOwnAttributes(node);
  node.querySelectorAll("[src],[href],[action]").forEach(rerouteOwnAttributes);
}};
new MutationObserver(function(records){{
  records.forEach(function(record){{
    if(record.type==="attributes")rerouteOwnAttributes(record.target);
    else record.addedNodes.forEach(rerouteTree);
  }});
}}).observe(document,{{subtree:true,childList:true,attributes:true,
  attributeFilter:["src","href","action"]}});
const NativeWebSocket=window.WebSocket;
window.WebSocket=class extends NativeWebSocket{{
  constructor(url,protocols){{super(route(url),protocols);}}
}};
const nativeFetch=window.fetch.bind(window);
window.fetch=function(input,init){{
  if(input instanceof Request) input=new Request(route(input.url),input);
  else input=route(input);
  return nativeFetch(input,init);
}};
const nativeOpen=XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open=function(method,url){{
  const args=Array.prototype.slice.call(arguments);args[1]=route(url);
  return nativeOpen.apply(this,args);
}};
if(window.EventSource){{
  const NativeEventSource=window.EventSource;
  window.EventSource=class extends NativeEventSource{{
    constructor(url,init){{super(route(url),init);}}
  }};
}}
}})();</script>"""


def rewrite_preview_html(
    data: bytes, prefix: str, project_routes: dict[str, str] | None = None
) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    text = re.sub(
        r'(?P<attr>\b(?:src|href|action)\s*=\s*["\'])/',
        rf"\g<attr>{prefix}",
        text,
        flags=re.IGNORECASE,
    )
    # Inline module scripts carry specifiers no attribute rewrite can reach --
    # @vitejs/plugin-react's refresh preamble imports "/@react-refresh" from an inline
    # <script type="module">. Left alone it 404s on the mux origin, the preamble never
    # runs, and every transformed module throws "can't detect preamble": a white page.
    # Runs before the bridge is injected so the bridge's own source is never rewritten.
    text = _rewrite_inline_scripts(text, prefix)
    bridge = _preview_runtime_bridge(prefix, project_routes)
    head = re.search(r"<head(?:\s[^>]*)?>", text, flags=re.IGNORECASE)
    if head:
        text = text[: head.end()] + bridge + text[head.end() :]
    else:
        text = bridge + text
    return text.encode("utf-8")


def rewrite_preview_css(data: bytes, prefix: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    text = re.sub(
        r"(?P<start>url\(\s*[\"']?)/(?P<tail>[^)\"']+)",
        rf"\g<start>{prefix}\g<tail>",
        text,
        flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


def rewrite_preview_javascript(data: bytes, prefix: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return _rewrite_javascript_text(text, prefix).encode("utf-8")


_JS_ROOT_SPECIFIER = re.compile(r"(?P<start>\b(?:from\s*|import\s*|import\s*\(\s*)[\"'])/")


_SCRIPT_ELEMENT = re.compile(
    r"(?P<open><script\b(?P<attrs>[^>]*)>)(?P<body>.*?)(?P<close></script\s*>)",
    flags=re.IGNORECASE | re.DOTALL,
)


_SCRIPT_TYPE = re.compile(r"""\btype\s*=\s*["']?([^"'\s>]+)""", flags=re.IGNORECASE)


_SCRIPT_SRC = re.compile(r"\bsrc\s*=", flags=re.IGNORECASE)


def _rewrite_javascript_text(text: str, prefix: str) -> str:
    return _JS_ROOT_SPECIFIER.sub(rf"\g<start>{prefix}", text)


def _rewrite_inline_scripts(text: str, prefix: str) -> str:
    """Prefix root-absolute module specifiers inside inline <script> bodies.

    Only bodies that the browser executes as JavaScript are touched; a data block
    (``application/json``, ``importmap``, ``text/template``) keeps its exact bytes.
    """

    def replace(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        if _SCRIPT_SRC.search(attrs):
            return match.group(0)
        declared = _SCRIPT_TYPE.search(attrs)
        mime = declared.group(1).casefold() if declared else ""
        if mime and mime != "module" and not ("javascript" in mime or "ecmascript" in mime):
            return match.group(0)
        body = _rewrite_javascript_text(match.group("body"), prefix)
        return f"{match.group('open')}{body}{match.group('close')}"

    return _SCRIPT_ELEMENT.sub(replace, text)


#: Content types a static preview serves by extension, ahead of ``mimetypes``.
#: Not a nicety: on Windows ``mimetypes`` consults the registry, where ``.js`` is
#: routinely registered as ``text/plain`` and ``.css`` sometimes is too. Combined
#: with the ``X-Content-Type-Options: nosniff`` every response here carries, that
#: renders the page unstyled and scriptless with nothing in the network log to
#: explain it. The web types are therefore stated, not asked for.
_STATIC_PREVIEW_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    ".xhtml": "application/xhtml+xml; charset=utf-8",
}


#: A static preview document is served from the daemon's own origin, and this
#: origin *is* the authority - swe-mux has no login, so anything same-origin can
#: drive the API. The in-app iframe already withholds ``allow-same-origin``, but
#: the pane's `external` button navigates to this route directly, where nothing
#: else would. A CSP ``sandbox`` puts the document in an opaque origin however it
#: was reached, so its scripts run and its `Origin: null` mutations are refused by
#: `security_middleware` outside `/preview/`. `frame-ancestors 'self'` is restated
#: because setting this header at all replaces the blanket preview CSP.
_STATIC_PREVIEW_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-modals; "
    "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'; "
    "connect-src * data: blob:; frame-ancestors 'self'"
)


def static_preview_content_type(relative_path: str) -> str:
    suffix = PurePosixPath(relative_path).suffix.casefold()
    stated = _STATIC_PREVIEW_CONTENT_TYPES.get(suffix)
    if stated:
        return stated
    guessed, _encoding = mimetypes.guess_type(relative_path)
    return guessed or "application/octet-stream"


async def _serve_static_preview(
    request: web.Request,
    item: Any,
    prefix: str,
    project_routes: dict[str, str],
) -> web.Response:
    """Serve one file from a static preview's directory, through the proxy route.

    The same rewriting the loopback proxy applies is applied here, so a page's
    root-relative `/app.css` resolves under the served directory instead of
    hitting the mux origin, and the runtime bridge still reaches sibling Project
    services. Read-only by construction: a Preview is a viewport, and there is no
    upstream here to give a write any meaning.
    """
    if request.method not in {"GET", "HEAD"}:
        raise web.HTTPMethodNotAllowed(request.method, ["GET", "HEAD"])
    tail = request.match_info.get("tail", "")
    try:
        data, resolved, size = await asyncio.to_thread(
            read_static_preview_file, item.doc_root, tail, item.entry, PREVIEW_RESPONSE_BYTES
        )
    except FileNotFoundError:
        raise web.HTTPNotFound(text="no such file in this preview") from None
    except ValueError as exc:
        # Containment and unreadable-file refusals arrive the same way; neither is
        # a server fault and neither should echo a filesystem path back.
        log.debug("static preview refused %s (%s)", item.id, exc)
        raise web.HTTPForbidden(text="preview path is not inside the served directory") from None
    if data is None:
        raise web.HTTPRequestEntityTooLarge(max_size=PREVIEW_RESPONSE_BYTES, actual_size=size)
    content_type = static_preview_content_type(resolved)
    casefolded = content_type.casefold()
    if "html" in casefolded:
        data = rewrite_preview_html(data, prefix, project_routes)
    elif "text/css" in casefolded:
        data = rewrite_preview_css(data, prefix)
    elif any(marker in casefolded for marker in ("javascript", "ecmascript")):
        data = rewrite_preview_javascript(data, prefix)
    headers = {
        "Content-Type": content_type,
        # A Preview is a development viewport: revalidate every resource so
        # editing the file and pressing refresh cannot show yesterday's bytes.
        "Cache-Control": "no-cache",
        "Content-Security-Policy": _STATIC_PREVIEW_CSP,
    }
    if request.headers.get("Origin") == "null":
        # The sandboxed document is its own opaque origin, so its own `fetch` of a
        # sibling asset is cross-origin. Same narrow allowance the loopback proxy
        # makes, and scoped to this route the same way.
        headers["Access-Control-Allow-Origin"] = "null"
        headers["Vary"] = "Origin"
    if request.method == "HEAD":
        return web.Response(body=b"", headers=headers)
    return web.Response(body=data, headers=headers)


def preview_target(item: Any, tail: str, query: str = "") -> tuple[str, str]:
    parsed = urlsplit(item.url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("preview registration is no longer a valid loopback destination")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("preview registration is invalid")
    path = f"{parsed.path.rstrip('/')}/{tail.lstrip('/')}"
    target = urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return target, origin


def _preview_request_headers(request: web.Request, upstream_origin: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.casefold() not in _PROXY_HOP_HEADERS
        and name.casefold()
        not in {
            "host",
            "origin",
            "referer",
            "content-length",
            "x-mux-hook-secret",
        }
        and not name.casefold().startswith("sec-websocket-")
    }
    headers["Origin"] = upstream_origin
    headers["Referer"] = f"{upstream_origin}/"
    return headers


async def _acquire_preview_slot(semaphore: asyncio.Semaphore) -> None:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
    except TimeoutError as exc:
        raise web.HTTPServiceUnavailable(
            text="preview proxy is at its concurrency limit",
            headers={"Retry-After": "1"},
        ) from exc


async def _proxy_websocket(request: web.Request, target: str, origin: str) -> web.WebSocketResponse:
    semaphore: asyncio.Semaphore = request.app[keys.PREVIEW_WS_SEMAPHORE]
    await _acquire_preview_slot(semaphore)
    offered_protocols = tuple(
        value.strip()
        for value in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if value.strip()
    )
    client = ClientSession(timeout=ClientTimeout(total=None, sock_connect=10))
    upstream = None
    downstream = None
    try:
        upstream = await client.ws_connect(
            target,
            headers=_preview_request_headers(request, origin),
            protocols=offered_protocols,
            autoclose=False,
            autoping=False,
            max_msg_size=PREVIEW_WS_MESSAGE_BYTES,
        )
        selected = (upstream.protocol,) if upstream.protocol else ()
        downstream = metered_websocket(
            request,
            "preview",
            protocols=selected,
            autoclose=False,
            autoping=False,
            max_msg_size=PREVIEW_WS_MESSAGE_BYTES,
        )
        await downstream.prepare(request)

        async def relay(source: Any, destination: Any) -> None:
            # unsupervised-loop-ok: lives for one preview websocket, not the daemon.
            while True:
                message = await asyncio.wait_for(source.receive(), timeout=PREVIEW_WS_IDLE_SECONDS)
                if message.type == WSMsgType.TEXT:
                    await destination.send_str(message.data)
                elif message.type == WSMsgType.BINARY:
                    await destination.send_bytes(message.data)
                elif message.type == WSMsgType.PING:
                    await destination.ping(message.data)
                elif message.type == WSMsgType.PONG:
                    await destination.pong(message.data)
                elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    return

        async with asyncio.timeout(PREVIEW_WS_LIFETIME_SECONDS):
            tasks = {
                asyncio.create_task(relay(downstream, upstream)),
                asyncio.create_task(relay(upstream, downstream)),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
    except (ClientError, OSError, TimeoutError) as exc:
        if downstream is None:
            raise web.HTTPBadGateway(text=f"preview websocket unavailable: {exc}") from exc
        await downstream.close(code=1011, message=b"preview websocket unavailable")
    finally:
        if upstream is not None and not upstream.closed:
            with suppress(Exception):
                await upstream.close()
        if downstream is not None and not downstream.closed:
            with suppress(Exception):
                await downstream.close()
        await client.close()
        semaphore.release()
    if downstream is None:  # pragma: no cover - pre-prepare failures raise above
        raise web.HTTPBadGateway(text="preview websocket unavailable")
    return downstream


async def preview_proxy(request: web.Request) -> web.StreamResponse:
    if request.method in {"CONNECT", "TRACE"}:
        raise web.HTTPMethodNotAllowed(
            request.method, ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        )
    preview_id = request.match_info["preview_id"]
    item = request.app[keys.PREVIEWS].items.get(preview_id)
    if item is None:
        raise web.HTTPNotFound(text="preview registration not found")
    static = getattr(item, "kind", "loopback") == "static"
    # Gated on the kind, never on "session_id is falsy". A loopback preview points
    # at a listener a session owns, so an ended session means the destination is
    # gone. A static preview points at bytes on disk in a Project that outlives
    # every session, and has no owning session to check in the first place.
    if not static and item.session_id not in request.app[keys.SESSIONS].sessions:
        raise web.HTTPGone(text="preview session is no longer live")
    tail = request.match_info.get("tail", "")
    previews = request.app[keys.PREVIEWS]
    ensure_detected = getattr(previews, "ensure_detected", None)
    if ensure_detected is not None:
        await ensure_detected(item.project_id)
    routes_for_project = getattr(previews, "routes_for_project", None)
    project_routes = routes_for_project(item.project_id) if routes_for_project else {}
    if static:
        return await _serve_static_preview(
            request, item, f"/preview/{preview_id}/", project_routes
        )
    target, origin = preview_target(item, tail, request.query_string)
    if request.headers.get("Upgrade", "").casefold() == "websocket":
        return await _proxy_websocket(request, target, origin)
    if request.content_length is not None and request.content_length > PREVIEW_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=PREVIEW_REQUEST_BYTES, actual_size=request.content_length
        )
    body = await request.read()
    if len(body) > PREVIEW_REQUEST_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=PREVIEW_REQUEST_BYTES, actual_size=len(body))
    semaphore: asyncio.Semaphore = request.app[keys.PREVIEW_HTTP_SEMAPHORE]
    await _acquire_preview_slot(semaphore)
    try:
        # Per-operation timeouts (not a wall-clock total) so a legitimately large
        # or slow passthrough download is not aborted mid-stream, while a hung
        # upstream still trips sock_read and a dead loopback port fails fast.
        async with ClientSession(
            timeout=ClientTimeout(total=None, sock_connect=10, sock_read=30)
        ) as client:
            async with client.request(
                request.method,
                target,
                headers=_preview_request_headers(request, origin),
                data=body or None,
                allow_redirects=False,
            ) as upstream:
                if (
                    upstream.content_length is not None
                    and upstream.content_length > PREVIEW_RESPONSE_BYTES
                ):
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=PREVIEW_RESPONSE_BYTES,
                        actual_size=upstream.content_length,
                    )
                content_type = upstream.headers.get("Content-Type", "")
                casefolded = content_type.casefold()
                prefix = f"/preview/{preview_id}/"
                needs_rewrite = (
                    "text/html" in casefolded
                    or "text/css" in casefolded
                    or any(m in casefolded for m in ("javascript", "ecmascript", "typescript"))
                )
                # Build the outbound headers (whitelist + Location reject/rewrite +
                # CORS-null) up front so an external redirect still fails with a 502
                # BEFORE any bytes are written on the streaming path.
                response_headers = {
                    name: value
                    for name, value in upstream.headers.items()
                    if name.casefold() in _PROXY_RESPONSE_HEADERS
                    and name.casefold() not in {"cache-control", "expires"}
                }
                # A Preview is a development viewport. Revalidate every resource so
                # replacing same-URL HTML, bundles, or images cannot require a new
                # port merely to escape an upstream max-age cache entry.
                response_headers["Cache-Control"] = "no-cache"
                location = upstream.headers.get("Location")
                if location:
                    resolved = urlsplit(origin)._replace(path="", query="", fragment="")
                    destination = urlsplit(location)
                    if destination.hostname and (
                        destination.hostname != resolved.hostname
                        or destination.port != resolved.port
                        or destination.scheme != resolved.scheme
                    ):
                        raise web.HTTPBadGateway(
                            text="preview upstream attempted an external redirect"
                        )
                    response_headers["Location"] = prefix + destination.path.lstrip("/")
                    if destination.query:
                        response_headers["Location"] += f"?{destination.query}"
                if request.headers.get("Origin") == "null":
                    response_headers["Access-Control-Allow-Origin"] = "null"
                    response_headers["Vary"] = "Origin"
                    if request.method == "OPTIONS":
                        response_headers["Access-Control-Allow-Methods"] = (
                            "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
                        )
                        requested_headers = request.headers.get(
                            "Access-Control-Request-Headers", ""
                        )[:1000]
                        if requested_headers:
                            response_headers["Access-Control-Allow-Headers"] = requested_headers
                if request.method == "HEAD":
                    return web.Response(body=b"", status=upstream.status, headers=response_headers)
                if needs_rewrite:
                    # HTML/CSS/JS rewriting needs the whole body, so buffer it (with the
                    # running-total cap) and rewrite before responding.
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in upstream.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > PREVIEW_RESPONSE_BYTES:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=PREVIEW_RESPONSE_BYTES, actual_size=total
                            )
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if "text/html" in casefolded:
                        data = rewrite_preview_html(data, prefix, project_routes)
                    elif "text/css" in casefolded:
                        data = rewrite_preview_css(data, prefix)
                    else:
                        data = rewrite_preview_javascript(data, prefix)
                    return web.Response(body=data, status=upstream.status, headers=response_headers)
                # Passthrough: stream unrewritten bodies straight through instead of
                # materialising up to PREVIEW_RESPONSE_BYTES in daemon RAM per request.
                response = web.StreamResponse(status=upstream.status, headers=response_headers)
                # Only advertise the upstream Content-Length for an identity-encoded
                # body. aiohttp auto-decompresses gzip/deflate/br, so we stream the
                # DECOMPRESSED bytes while ``upstream.content_length`` is the raw
                # (compressed) header value; copying it would make aiohttp truncate the
                # outbound body to the compressed length (a silent fail-open). Leaving
                # it unset makes aiohttp chunk-frame the decompressed stream. The
                # response never carries Content-Encoding (not whitelisted), so the
                # client correctly receives already-decompressed bytes.
                content_encoding = upstream.headers.get("Content-Encoding", "").strip().casefold()
                if (
                    content_encoding in ("", "identity")
                    and upstream.content_length is not None
                    and upstream.content_length <= PREVIEW_RESPONSE_BYTES
                ):
                    response.content_length = upstream.content_length
                # The security middleware stamps its headers only after the handler
                # returns, which is too late once we prepare() and stream ourselves.
                apply_security_headers(response, request)
                await response.prepare(request)
                total = 0
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > PREVIEW_RESPONSE_BYTES:
                        # Headers are already sent, so a clean 413 is impossible: abort
                        # the connection so the client sees a broken transfer, never a
                        # well-formed response carrying a silently truncated body.
                        if request.transport is not None:
                            request.transport.close()
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=PREVIEW_RESPONSE_BYTES, actual_size=total
                        )
                    await response.write(chunk)
                await response.write_eof()
                return response
    except (ClientError, OSError, TimeoutError) as exc:
        raise web.HTTPBadGateway(text=f"preview unavailable: {exc}") from exc
    finally:
        semaphore.release()

