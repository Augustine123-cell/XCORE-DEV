"""SSRF guard: only public http(s) targets, no embedded credentials, no private IPs."""
import asyncio
import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit


class UrlBlocked(ValueError):
    """Raised with a user-facing message when a URL is not allowed."""


def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True  # unparseable → treat as unsafe
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not addr.is_global  # private, loopback, link-local, CGNAT, reserved, multicast…


async def assert_safe_url(raw: str) -> SplitResult:
    try:
        u = urlsplit(str(raw).strip())
        host = (u.hostname or "").lower()
        _ = u.port  # raises ValueError on a bad port
    except ValueError:
        raise UrlBlocked("Invalid URL.") from None
    if not u.scheme:
        raise UrlBlocked("Invalid URL.")
    if u.scheme not in ("http", "https"):
        raise UrlBlocked("Only http:// and https:// URLs are allowed.")
    if not host:
        raise UrlBlocked("Invalid URL.")
    if u.username or u.password:
        raise UrlBlocked("URLs with embedded credentials are not allowed.")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise UrlBlocked("Local/private hosts are not allowed.")
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        if is_private_ip(host):
            raise UrlBlocked("Private/local IP addresses are not allowed.")
        return u
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise UrlBlocked("Host could not be resolved.") from None
    if not infos or any(is_private_ip(i[4][0]) for i in infos):
        raise UrlBlocked("Host resolves to a private/local address and was blocked.")
    return u
