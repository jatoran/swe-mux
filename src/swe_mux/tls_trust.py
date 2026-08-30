"""One verifying TLS client context, built from what this machine actually trusts.

On 2026-08-30 a clean Windows 11 laptop could not download the Kokoro pronunciation
model: `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate`
against `github.com`, from a machine whose browser reached the same URL fine.

The usual diagnosis - "Python on Windows does not use the OS certificate store" -
is wrong, and getting it wrong picks the weaker fix. `ssl.create_default_context()`
*does* enumerate the Windows ROOT store. What it does not do is trigger Windows'
on-demand root update: the OS ships a small root set and fetches the rest through
CryptoAPI the first time a chain needs one, and `ssl.enum_certificates` only lists
what has already been fetched. A machine nobody has browsed much on therefore has
a ROOT store that is genuinely missing the issuer, and every non-Microsoft TLS
client on it fails while Edge succeeds.

`truststore` is the fix that matches the cause: it hands the peer chain to the
platform's own verifier (`CertGetCertificateChain` on Windows, Security.framework
on macOS), which is the code path that performs the fetch. Carrying a `certifi`
bundle instead would also work for public roots and would silently *break* a host
behind a corporate TLS-inspecting proxy, whose issuing CA is in the OS store and
in no bundle. certifi is kept only as the fallback for a platform truststore does
not implement.

**Client contexts only, deliberately.** `truststore.inject_into_ssl()` is the
documented one-liner and would have been wrong here: it replaces `ssl.SSLContext`
globally, and its `wrap_socket` verifies the peer chain unconditionally - so
swe-mux's own HTTPS listener (`tailscale.direct_mobile_voice_tls`), which asks for
no client certificate, would raise "Peer sent no certificates to verify" on every
browser connection. A daemon that cannot serve the phone is a worse bug than the
one being fixed. Everything here is opt-in per call site and passed as `ssl=` to
the connector that needs it.
"""

from __future__ import annotations

import functools
import logging
import ssl
from typing import Any

log = logging.getLogger(__name__)


@functools.cache
def client_ssl_context() -> ssl.SSLContext:
    """A verifying context for outbound HTTPS, trusting this OS's own roots.

    Cached: building one costs a store enumeration, every caller wants the same
    answer, and an `SSLContext` is safe to share across connections and threads.

    Never returns an unverified context and never raises. If both trust sources
    are unavailable the stdlib default is returned - which is exactly today's
    behaviour, so the worst case of this module is that it changes nothing.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - any failure here falls back, never propagates
        log.debug("truststore unavailable; falling back to a bundled CA context", exc_info=True)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - same reasoning
        log.debug("certifi unavailable; falling back to the stdlib default", exc_info=True)
    return ssl.create_default_context()


def trusting_connector(**kwargs: Any) -> Any:
    """An `aiohttp.TCPConnector` that verifies against the OS trust store.

    A helper rather than a bare `ssl=` argument at each call site so that the
    reasoning above lives in one place and a new outbound fetch inherits it by
    using this instead of remembering to.
    """
    import aiohttp

    return aiohttp.TCPConnector(ssl=client_ssl_context(), **kwargs)


def describe_trust_source() -> str:
    """Which verifier the context above will use. For diagnostics, not for logic."""
    context = client_ssl_context()
    module = type(context).__module__.split(".", 1)[0]
    return "os-trust-store" if module == "truststore" else "bundled-ca"
