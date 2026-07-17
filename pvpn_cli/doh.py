"""Mandatory DNS-over-HTTPS resolver for all CLI network traffic.

Public hostnames are resolved through trusted DoH endpoints contacted by IP,
so bootstrapping never depends on the system or ISP DNS resolver. Resolution
fails closed if every DoH endpoint is unavailable; there is intentionally no
plain-DNS fallback.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple
from urllib.parse import quote


_ORIGINAL_GETADDRINFO = socket.getaddrinfo


@dataclass(frozen=True)
class _DoHEndpoint:
    hostname: str
    address: str
    path: str


_ENDPOINTS = (
    _DoHEndpoint("cloudflare-dns.com", "1.1.1.1", "/dns-query"),
    _DoHEndpoint("dns.google", "8.8.8.8", "/resolve"),
)


class DoHResolutionError(socket.gaierror):
    """Raised when encrypted DNS resolution cannot be completed."""


class DoHResolver:
    def __init__(self, endpoints=_ENDPOINTS, timeout: float = 5.0):
        self._endpoints = endpoints
        self._timeout = timeout
        self._cache: Dict[Tuple[str, int], Tuple[float, List[str]]] = {}
        self._lock = threading.RLock()
        self._ssl_context = ssl.create_default_context()

    def resolve(self, hostname: str, family: int = socket.AF_UNSPEC) -> List[str]:
        normalized = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        cache_key = (normalized, family)
        now = time.monotonic()

        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return list(cached[1])

        addresses, ttl = self._resolve_uncached(normalized, family)
        if not addresses:
            raise DoHResolutionError(socket.EAI_NONAME, f"DoH returned no addresses for {hostname}")

        # Avoid permanently caching stale records and avoid retry storms.
        expires_at = now + max(30, min(ttl, 3600))
        with self._lock:
            self._cache[cache_key] = (expires_at, list(addresses))
        return addresses

    def _resolve_uncached(self, hostname: str, family: int) -> Tuple[List[str], int]:
        query_types = []
        if family in (socket.AF_UNSPEC, socket.AF_INET):
            query_types.append(("A", 1))
        if family in (socket.AF_UNSPEC, socket.AF_INET6):
            query_types.append(("AAAA", 28))
        if not query_types:
            raise DoHResolutionError(socket.EAI_FAMILY, f"Unsupported address family: {family}")

        errors = []
        for endpoint in self._endpoints:
            try:
                addresses: List[str] = []
                ttls: List[int] = []
                for query_name, record_type in query_types:
                    answers = self._query_endpoint(endpoint, hostname, query_name)
                    for answer in answers:
                        if answer.get("type") != record_type:
                            continue
                        value = answer.get("data", "")
                        try:
                            ipaddress.ip_address(value)
                        except ValueError:
                            continue
                        addresses.append(value)
                        ttls.append(int(answer.get("TTL", 60)))
                if addresses:
                    return list(dict.fromkeys(addresses)), min(ttls or [60])
            except Exception as exc:  # try the next encrypted resolver
                errors.append(f"{endpoint.hostname}: {exc}")

        detail = "; ".join(errors) or "all DoH resolvers returned empty answers"
        raise DoHResolutionError(socket.EAI_AGAIN, f"Secure DNS resolution failed for {hostname}: {detail}")

    def _query_endpoint(self, endpoint: _DoHEndpoint, hostname: str, query_type: str):
        path = f"{endpoint.path}?name={quote(hostname, safe='')}&type={query_type}"
        raw_socket = socket.create_connection((endpoint.address, 443), timeout=self._timeout)
        tls_socket = None
        try:
            # Certificate validation is performed against the resolver hostname,
            # while the TCP connection uses a pinned bootstrap IP.
            tls_socket = self._ssl_context.wrap_socket(
                raw_socket,
                server_hostname=endpoint.hostname,
            )
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {endpoint.hostname}\r\n"
                "Accept: application/dns-json\r\n"
                "User-Agent: ProtonVPN-Next-CLI/DoH\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            tls_socket.sendall(request)

            response = http.client.HTTPResponse(tls_socket)
            response.begin()
            body = response.read()
            if response.status != 200:
                raise OSError(f"HTTP {response.status}")
            payload = json.loads(body.decode("utf-8"))
            if payload.get("Status") != 0:
                raise OSError(f"DNS status {payload.get('Status')}")
            return payload.get("Answer", [])
        finally:
            if tls_socket is not None:
                tls_socket.close()
            else:
                raw_socket.close()


_RESOLVER = DoHResolver()
_INSTALL_LOCK = threading.Lock()
_INSTALLED = False


def _is_local_or_numeric(host: object) -> bool:
    if host is None or not isinstance(host, str):
        return True
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost") or normalized.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(normalized)
        return True
    except ValueError:
        return False


def _secure_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if _is_local_or_numeric(host):
        return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)

    addresses = _RESOLVER.resolve(host, family)
    results = []
    seen = set()
    for address in addresses:
        resolved = _ORIGINAL_GETADDRINFO(address, port, family, type, proto, flags)
        for item in resolved:
            if item not in seen:
                seen.add(item)
                results.append(item)
    if not results:
        raise DoHResolutionError(socket.EAI_NONAME, f"DoH returned no usable addresses for {host}")
    return results


def install_doh() -> None:
    """Install mandatory process-wide DoH resolution exactly once."""
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        socket.getaddrinfo = _secure_getaddrinfo
        _INSTALLED = True
