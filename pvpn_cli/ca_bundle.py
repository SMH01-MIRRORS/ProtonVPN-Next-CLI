"""Locate a usable CA bundle before any TLS context is created.

PyInstaller builds ship the OpenSSL of the machine that produced them, and that
OpenSSL keeps its trust store location baked in at compile time (OPENSSLDIR).
A binary built on Debian therefore looks for CA certificates under
/usr/lib/ssl. On a distribution that uses a different layout - Arch keeps them
in /etc/ssl - that directory does not exist at all, the default context loads
zero certificates, and every handshake fails with "unable to get local issuer
certificate", including the DoH bootstrap that all other traffic depends on.

The remedy is to point OpenSSL at a bundle that actually exists on this host
through SSL_CERT_FILE: the system store first, so locally installed CAs keep
working, and the certifi copy shipped inside the bundle as a fallback.
"""

from __future__ import annotations

import os
import ssl

# Trust stores of the common distributions, in the order we prefer them.
_SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch, Alpine
    "/etc/pki/tls/certs/ca-bundle.crt",    # Fedora, RHEL, CentOS
    "/etc/ssl/ca-bundle.pem",              # openSUSE
    "/etc/ssl/cert.pem",                   # Arch, Alpine, macOS
)


def _default_store_is_usable() -> bool:
    """Report whether OpenSSL finds any trust anchors on its own."""
    try:
        return bool(ssl.create_default_context().get_ca_certs())
    except Exception:
        return False


def _bundled_ca_bundle():
    """Path to the certifi bundle shipped with the frozen build, if present."""
    try:
        import certifi
    except ImportError:
        return None
    path = certifi.where()
    return path if os.path.isfile(path) else None


def ensure_ca_bundle() -> None:
    """Export SSL_CERT_FILE when OpenSSL cannot find a trust store by itself.

    Setting the environment variable rather than handing a context to each call
    site keeps every consumer covered - urllib, http.client, sentry - and also
    carries over to the elevated re-launch, which re-runs this bootstrap.
    """
    # An explicit operator override always wins.
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return

    if _default_store_is_usable():
        return

    for candidate in _SYSTEM_CA_BUNDLES:
        if os.path.isfile(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            return

    fallback = _bundled_ca_bundle()
    if fallback:
        os.environ["SSL_CERT_FILE"] = fallback
