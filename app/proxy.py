"""Proxy routing decisions.

Telegram traffic uses the configured proxy without silent direct fallback.
HTTP traffic (WebDAV) uses the proxy only for public destinations: internal
addresses always connect directly, because a remote proxy cannot reach a LAN
NAS or a Docker service such as ``http://openlist:5244``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

from app.config import ProxyConfig

INTERNAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost", ".localdomain", ".intranet", ".corp")

_RESOLVE_CACHE: dict[str, tuple[float, bool]] = {}
_RESOLVE_TTL_SECONDS = 300.0


def _is_internal_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        # Carrier-grade NAT (100.64.0.0/10) is used by Tailscale and similar.
        or (isinstance(address, ipaddress.IPv4Address) and address in ipaddress.ip_network("100.64.0.0/10"))
    )


def _matches_no_proxy(host: str, rules: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    for raw in rules:
        rule = raw.strip().lower()
        if not rule:
            continue
        if rule == "*":
            return True
        if address is not None:
            try:
                if address in ipaddress.ip_network(rule, strict=False):
                    return True
            except ValueError:
                pass
            continue
        rule = rule.lstrip("*").lstrip(".") if rule.startswith(("*", ".")) else rule
        if host == rule or host.endswith("." + rule):
            return True
    return False


def is_internal_host(host: str, no_proxy: tuple[str, ...] = ()) -> bool:
    """Static (no DNS) check whether ``host`` must bypass the proxy."""

    if not host:
        return True
    host = host.strip("[]").lower().rstrip(".")
    try:
        return _is_internal_ip(ipaddress.ip_address(host)) or _matches_no_proxy(host, no_proxy)
    except ValueError:
        pass
    if host == "localhost" or "." not in host:
        # Single-label names: Docker Compose services, /etc/hosts entries, LAN hosts.
        return True
    if host.endswith(INTERNAL_SUFFIXES):
        return True
    return _matches_no_proxy(host, no_proxy)


async def _resolves_to_internal(host: str, port: int) -> bool:
    now = time.monotonic()
    cached = _RESOLVE_CACHE.get(host)
    if cached and now - cached[0] < _RESOLVE_TTL_SECONDS:
        return cached[1]
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=5)
    except (OSError, asyncio.TimeoutError):
        # Unresolvable locally: let the proxy resolve it.
        _RESOLVE_CACHE[host] = (now, False)
        return False
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    result = bool(addresses) and all(_is_internal_ip(address) for address in addresses)
    _RESOLVE_CACHE[host] = (now, result)
    return result


async def http_proxy_for(url: str, proxy: ProxyConfig) -> str | None:
    """Return the proxy URL to use for an HTTP(S) destination, or ``None``."""

    if not proxy.enabled or not proxy.apply_to_webdav:
        return None
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if is_internal_host(host, proxy.no_proxy):
        return None
    if proxy.bypass_resolve:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if await _resolves_to_internal(host, port):
            return None
    return proxy.httpx_url()


def clear_resolve_cache() -> None:
    _RESOLVE_CACHE.clear()
