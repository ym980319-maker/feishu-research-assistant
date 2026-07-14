from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

import httpx

from .models import Evidence


DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_HTML_BYTES = 2_000_000
DEFAULT_MAX_PDF_BYTES = 20_000_000
DEFAULT_MAX_RETRIES = 1
DEFAULT_MAX_REDIRECTS = 3


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def env_int(name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def env_float(
    name: str, default: float, minimum: float = 0.1, maximum: float | None = None
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def httpx_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=env_float(
            "OFFICIAL_CONNECT_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT_SECONDS
        ),
        read=env_float("OFFICIAL_READ_TIMEOUT_SECONDS", DEFAULT_READ_TIMEOUT_SECONDS),
        write=env_float("OFFICIAL_READ_TIMEOUT_SECONDS", DEFAULT_READ_TIMEOUT_SECONDS),
        pool=env_float(
            "OFFICIAL_CONNECT_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT_SECONDS
        ),
    )


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def is_allowed_https_url(url: str, allowed_hosts: Iterable[str]) -> bool:
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return False
    try:
        if parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host not in {item.rstrip(".").lower() for item in allowed_hosts}:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host != "localhost"
    return is_public_ip(host)


async def resolves_to_public_ip(host: str) -> bool:
    try:
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(
            host,
            443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except (OSError, socket.gaierror):
        return False
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    return bool(addresses) and all(is_public_ip(address) for address in addresses)


async def validate_outbound_url(url: str, allowed_hosts: Iterable[str]) -> bool:
    if not is_allowed_https_url(url, allowed_hosts):
        return False
    host = urlsplit(url).hostname
    return bool(host) and await resolves_to_public_ip(host)


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    content: bytes
    final_url: str
    content_type: str


async def fetch_limited(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    allowed_hosts: Iterable[str],
    *,
    max_bytes: int,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_retries: int | None = None,
    **request_kwargs: Any,
) -> FetchResult | None:
    retries = (
        env_int("OFFICIAL_MAX_RETRIES", DEFAULT_MAX_RETRIES, minimum=0, maximum=3)
        if max_retries is None
        else max(0, min(max_retries, 3))
    )
    method = method.upper()

    for attempt in range(retries + 1):
        current_url = url
        redirects = 0
        try:
            while True:
                if not await validate_outbound_url(current_url, allowed_hosts):
                    return None
                async with client.stream(
                    method,
                    current_url,
                    follow_redirects=False,
                    **request_kwargs,
                ) as response:
                    if response.is_redirect:
                        if method not in {"GET", "HEAD"} or redirects >= max_redirects:
                            return FetchResult(
                                response.status_code, b"", current_url, ""
                            )
                        location = response.headers.get("location")
                        if not location:
                            return None
                        current_url = urljoin(current_url, location)
                        redirects += 1
                        continue

                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                return None
                        except ValueError:
                            pass

                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            return None
                        chunks.append(chunk)
                    return FetchResult(
                        status_code=response.status_code,
                        content=b"".join(chunks),
                        final_url=str(response.url),
                        content_type=response.headers.get("content-type", ""),
                    )
        except (httpx.HTTPError, OSError):
            if attempt >= retries:
                return None
            await asyncio.sleep(0.2 * (attempt + 1))
    return None


class EvidenceProvider(ABC):
    name: str
    allowed_hosts: frozenset[str]

    @abstractmethod
    async def search(
        self,
        subject: dict[str, Any],
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[Evidence]:
        raise NotImplementedError
