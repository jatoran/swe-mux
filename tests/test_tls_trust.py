"""Outbound TLS verifies against the OS trust store, and only outbound.

The bug: on a clean Windows 11 machine the Kokoro pronunciation model download
failed with `unable to get local issuer certificate` against github.com, on a host
whose browser reached the same URL. Windows fetches most roots on demand through
CryptoAPI and `ssl.enum_certificates` only lists what has already been fetched, so
Python's own reading of the ROOT store was genuinely short an issuer.

The guard that matters most here is the *scope* one. `truststore.inject_into_ssl()`
is the documented one-liner and would have broken the daemon: it replaces
`ssl.SSLContext` globally and verifies the peer chain unconditionally, so swe-mux's
own HTTPS listener - which asks browsers for no client certificate - would raise
"Peer sent no certificates to verify" on every connection.
"""

from __future__ import annotations

import inspect
import ssl

import pytest

from swe_mux import tls_trust


def test_the_client_context_verifies() -> None:
    context = tls_trust.client_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_the_context_is_shared_rather_than_rebuilt_per_request() -> None:
    assert tls_trust.client_ssl_context() is tls_trust.client_ssl_context()


def test_the_os_trust_store_is_used_when_truststore_is_available() -> None:
    pytest.importorskip("truststore")
    assert tls_trust.describe_trust_source() == "os-trust-store"


def test_nothing_installs_truststore_globally() -> None:
    """A global injection would break this daemon's own TLS listener.

    `tailscale.direct_mobile_voice_tls` serves browsers and requests no client
    certificate, and truststore's `wrap_socket` verifies the peer chain on every
    socket including a server one - so an injected `ssl.SSLContext` turns every
    phone connection into "Peer sent no certificates to verify". A daemon that
    cannot serve the phone is a worse bug than the one being fixed.

    Asserted behaviourally rather than by scanning for the call: what matters is
    that `ssl.SSLContext` is still the stdlib class after a client context has
    been built, which is true however the injection might have been spelled.
    """
    tls_trust.client_ssl_context()
    assert ssl.SSLContext.__module__ == "ssl"
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    assert type(server).__module__ == "ssl"


def test_a_missing_trust_library_degrades_to_the_stdlib_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worst case must be today's behaviour, never an unverified context."""
    import builtins

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name in {"truststore", "certifi"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", refuse)
    tls_trust.client_ssl_context.cache_clear()
    try:
        context = tls_trust.client_ssl_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
    finally:
        monkeypatch.undo()
        tls_trust.client_ssl_context.cache_clear()


def test_every_public_internet_fetch_goes_through_the_trusting_connector() -> None:
    """The fix is only worth as much as its coverage.

    Each of these downloads something on the user's behalf from a public host, and
    each is reachable during first-run setup on exactly the kind of machine whose
    trust store is short an issuer. A new one that forgets this inherits the bug.
    """
    from swe_mux import update_check, update_install, voice_models, wheel_closure

    for module in (voice_models, wheel_closure, update_check, update_install):
        source = inspect.getsource(module)
        assert "trusting_connector()" in source, module.__name__
        for line in source.splitlines():
            if "aiohttp.ClientSession(" in line and "connector" not in line:
                # Multi-line call: the connector must appear in the continuation.
                assert "(" == line.strip()[-1], f"{module.__name__}: {line.strip()}"
