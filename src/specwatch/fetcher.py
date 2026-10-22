"""Bounded and conditional HTTP fetching for remote OpenAPI documents."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from specwatch.config import Settings
from specwatch.errors import InvalidSpecURL, SpecFetchError


@dataclass(frozen=True, slots=True)
class FetchResult:
    content: bytes | None
    content_type: str
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


def validate_url_shape(url: str) -> str:
    """Reject URL features that are unsafe or not supported by the fetcher."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise InvalidSpecURL("The specification URL is malformed.") from exc
    if parsed.scheme.lower() != "https":
        raise InvalidSpecURL("Specification URLs must use HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise InvalidSpecURL("Provide an HTTPS URL without embedded credentials.")
    if port not in (None, 443):
        raise InvalidSpecURL("Only the standard HTTPS port (443) is supported.")
    if parsed.fragment:
        raise InvalidSpecURL("Specification URLs cannot include a fragment.")
    return parsed.hostname.rstrip(".").lower()


class SpecFetcher:
    """Fetch public HTTPS specs with size caps, no redirects, and safe defaults."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> SpecFetcher:
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json, application/yaml, text/yaml;q=0.9, */*;q=0.1",
                "User-Agent": "SpecWatch/0.1 (+https://github.com/)",
            },
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None

    async def _validate_public_host(self, host: str) -> None:
        if self.settings.allowed_hosts and host not in self.settings.allowed_hosts:
            raise InvalidSpecURL("This host is not in SPECWATCH_ALLOWED_HOSTS.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
                raise InvalidSpecURL("Local hostnames are not allowed.") from None
            try:
                records = await asyncio.get_running_loop().getaddrinfo(
                    host, 443, type=socket.SOCK_STREAM
                )
            except socket.gaierror as exc:
                raise SpecFetchError("The specification host could not be resolved.") from exc
            addresses = {record[4][0] for record in records}
        else:
            addresses = {str(address)}

        if not addresses:
            raise SpecFetchError("The specification host did not resolve to an address.")
        if any(not ipaddress.ip_address(item).is_global for item in addresses):
            raise InvalidSpecURL("Specification hosts must resolve only to public IP addresses.")

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        if not self.client:
            raise RuntimeError("SpecFetcher must be used as an async context manager.")
        host = validate_url_shape(url)
        await self._validate_public_host(host)

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        for attempt in range(3):
            try:
                async with self.client.stream("GET", url, headers=headers) as response:
                    response_etag = response.headers.get("etag")
                    response_modified = response.headers.get("last-modified")
                    if response.status_code == 304:
                        return FetchResult(None, "", response_etag, response_modified, True)
                    if 300 <= response.status_code < 400:
                        raise SpecFetchError(
                            "The specification URL redirected; redirects are disabled."
                        )
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            retry_after = response.headers.get("retry-after", "")
                            try:
                                delay = min(max(float(retry_after), 0), 2)
                            except ValueError:
                                delay = 0.25 * (2**attempt)
                            await asyncio.sleep(delay)
                            continue
                    if response.status_code < 200 or response.status_code >= 300:
                        raise SpecFetchError(
                            f"The specification server returned HTTP {response.status_code}."
                        )
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            if int(length) > self.settings.max_spec_bytes:
                                raise SpecFetchError(
                                    "The specification exceeds the configured size limit."
                                )
                        except ValueError as exc:
                            raise SpecFetchError(
                                "The specification server sent an invalid content length."
                            ) from exc
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self.settings.max_spec_bytes:
                            raise SpecFetchError(
                                "The specification exceeds the configured size limit."
                            )
                        chunks.append(chunk)
                    return FetchResult(
                        b"".join(chunks),
                        response.headers.get("content-type", ""),
                        response_etag,
                        response_modified,
                    )
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise SpecFetchError(
                        "The specification server timed out after three attempts."
                    ) from exc
                await asyncio.sleep(0.25 * (2**attempt))
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise SpecFetchError(
                        "The specification server could not be reached after three attempts."
                    ) from exc
                await asyncio.sleep(0.25 * (2**attempt))

        raise SpecFetchError("The specification could not be fetched.")
