"""Controlled fetching (section 10, 25): SSRF defence, redirect/size limits, MIME validation,
decompression-bomb protection. All fetched content is untrusted data, never instructions."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx

from content_factory.schemas.base import sha256_hex

MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_S = 20.0
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/pdf",
    "application/json",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
}
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard the above",
    "you are now",
    "system prompt",
    "<|im_start|>",
    "developer message",
)

Resolver = Callable[[str], list[str]]


class FetchError(Exception):
    pass


class SSRFBlockedError(FetchError):
    pass


@dataclass(frozen=True)
class CapturedContent:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    sha256: str
    injection_flags: tuple[str, ...]


def _default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError(f"DNS resolution failed for {host!r}: {exc}") from exc
    return sorted({str(info[4][0]) for info in infos})


def _assert_public(url: str, resolver: Resolver) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"scheme {parsed.scheme!r} is not allowed")
    if not parsed.hostname:
        raise SSRFBlockedError("URL has no hostname")
    if parsed.username or parsed.password:
        raise SSRFBlockedError("credentials in URLs are not allowed")
    host = parsed.hostname
    try:
        candidates = [str(ipaddress.ip_address(host))]
    except ValueError:
        candidates = resolver(host)
    if not candidates:
        raise SSRFBlockedError(f"{host!r} resolves to no address")
    for addr in candidates:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or (
                ip.version == 6
                and ip.ipv4_mapped is not None
                and not ipaddress.ip_address(ip.ipv4_mapped).is_global
            )
        ):
            raise SSRFBlockedError(f"{host!r} resolves to non-public address {ip}")


def _canonicalize(url: str) -> str:
    p = urlparse(url)
    netloc = (p.hostname or "").lower() + (
        f":{p.port}" if p.port and p.port not in (80, 443) else ""
    )
    query = "&".join(
        sorted(
            q
            for q in p.query.split("&")
            if q and not q.lower().startswith(("utm_", "fbclid", "gclid"))
        )
    )
    return urlunparse((p.scheme.lower(), netloc, p.path.rstrip("/") or "/", "", query, ""))


def canonical_url(url: str) -> str:
    return _canonicalize(url)


def scan_injection(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(m for m in _INJECTION_MARKERS if m in lowered)


def safe_fetch(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    resolver: Resolver | None = None,
    max_bytes: int = MAX_BODY_BYTES,
    allowed_content_types: set[str] | None = None,
    egress_allowlist: tuple[str, ...] | None = None,
) -> CapturedContent:
    """Fetch one public URL with every hop re-validated. Raises on any policy violation."""
    resolver = resolver or _default_resolver
    allowed = allowed_content_types or ALLOWED_CONTENT_TYPES
    current = url
    client = httpx.Client(
        transport=transport,
        timeout=TIMEOUT_S,
        follow_redirects=False,
        headers={"User-Agent": "content-factory-research/0.1 (+local-first)"},
    )
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            _assert_public(current, resolver)
            if egress_allowlist is not None:
                host = urlparse(current).hostname or ""
                if not any(host == d or host.endswith("." + d) for d in egress_allowlist):
                    raise SSRFBlockedError(f"host {host!r} is not on the egress allowlist")
            with client.stream("GET", current) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("redirect without location")
                    current = str(httpx.URL(current).join(location))
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} for {current}")
                declared = resp.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    raise FetchError(f"declared size {declared} exceeds cap {max_bytes}")
                content_type = (
                    (resp.headers.get("content-type") or "application/octet-stream")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if content_type not in allowed:
                    raise FetchError(f"content-type {content_type!r} is not allowed")
                # Stream with a hard cap: httpx decompresses, so this also bounds gzip bombs.
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(f"body exceeded cap {max_bytes} bytes (decompressed)")
                    chunks.append(chunk)
                body = b"".join(chunks)
                text_probe = (
                    body[:200_000].decode("utf-8", errors="ignore")
                    if content_type.startswith(("text/", "application/xhtml"))
                    else ""
                )
                return CapturedContent(
                    requested_url=url,
                    final_url=current,
                    status_code=resp.status_code,
                    content_type=content_type,
                    body=body,
                    sha256=sha256_hex(body),
                    injection_flags=scan_injection(text_probe),
                )
        raise FetchError(f"too many redirects (> {MAX_REDIRECTS})")
    finally:
        client.close()
