"""
PVPN Next CLI Package
"""

# A usable trust store has to be in place before anything builds an SSL
# context, and DoH builds one at import time, so this runs first.
from .ca_bundle import ensure_ca_bundle

ensure_ca_bundle()

# DNS-over-HTTPS is a mandatory security baseline for every CLI command.
# It is installed at package import time and intentionally has no opt-out.
from .doh import install_doh  # noqa: E402

install_doh()

__version__ = "0.1.0"
