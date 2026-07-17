"""
PVPN Next CLI Package
"""

# DNS-over-HTTPS is a mandatory security baseline for every CLI command.
# It is installed at package import time and intentionally has no opt-out.
from .doh import install_doh

install_doh()

__version__ = "0.1.0"
