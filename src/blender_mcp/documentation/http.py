"""Bounded retrieval and extraction for official Blender documentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .constants import (
    MAX_PAGE_IDENTIFIER_CHARS,
    MAX_REDIRECTS,
    SAFE_API_PAGE_RE,
    SAFE_PAGE_RE,
)
from .context import (
    OFFICIAL_DOCUMENTATION_HOSTS,
    SOURCE_MANUAL,
    SOURCE_PYTHON_API,
    SOURCE_RELEASE_NOTES,
)


class BlenderDocumentationRetrievalError(RuntimeError):
    """Raised when an official documentation request cannot be completed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.url = url
        self.status_code = status_code

    def as_dict(self, *, source: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if source is not None:
            result["source"] = source
        if self.url is not None:
            result["url"] = self.url
        if self.status_code is not None:
            result["status_code"] = self.status_code
        return result


@dataclass(frozen=True)
class FetchedDocument:
    requested_url: str
    url: str
    status_code: int
    content_type: str
    content: bytes
    redirects: tuple[str, ...]
    etag: str | None = None
    last_modified: str | None = None
    cache: Mapping[str, Any] | None = None


def validate_official_url(url: str) -> str:
    """Validate an absolute official Blender HTTPS URL."""

    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in OFFICIAL_DOCUMENTATION_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
    ):
        raise BlenderDocumentationRetrievalError(
            "unsafe_url",
            "Documentation URL must be fragment-free HTTPS on an official Blender host",
        )
    return parsed.geturl()


class OfficialDocsFetcher:
    """Small HTTP client with redirect, type, timeout, and size boundaries."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.transport = transport
        self.timeout = timeout or httpx.Timeout(20.0, connect=5.0)

    def __call__(
        self,
        url: str,
        *,
        accepted_content_types: Iterable[str],
        max_bytes: int,
        request_headers: Mapping[str, str] | None = None,
    ) -> FetchedDocument:
        requested_url = validate_official_url(url)
        current_url = requested_url
        redirects: list[str] = []
        accepted = frozenset(item.lower() for item in accepted_content_types)
        headers = {
            "Accept": ", ".join(sorted(accepted)),
            "User-Agent": "blender-mcp-documentation/1",
        }
        if request_headers:
            headers.update(
                {str(key): str(value) for key, value in request_headers.items()}
            )

        with httpx.Client(
            follow_redirects=False,
            timeout=self.timeout,
            transport=self.transport,
            headers=headers,
        ) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                try:
                    with client.stream("GET", current_url) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise BlenderDocumentationRetrievalError(
                                    "invalid_redirect",
                                    "Official documentation redirect has no Location header",
                                    url=current_url,
                                )
                            if redirect_count >= MAX_REDIRECTS:
                                raise BlenderDocumentationRetrievalError(
                                    "too_many_redirects",
                                    f"Documentation request exceeded {MAX_REDIRECTS} redirects",
                                    url=current_url,
                                )
                            target = validate_official_url(
                                urljoin(current_url, location)
                            )
                            redirects.append(target)
                            current_url = target
                            continue

                        if response.status_code == 304:
                            return FetchedDocument(
                                requested_url=requested_url,
                                url=current_url,
                                status_code=304,
                                content_type="",
                                content=b"",
                                redirects=tuple(redirects),
                                etag=response.headers.get("etag"),
                                last_modified=response.headers.get("last-modified"),
                                cache={"status": "network_not_modified"},
                            )

                        if response.status_code != 200:
                            raise BlenderDocumentationRetrievalError(
                                "http_error",
                                f"Official documentation returned HTTP {response.status_code}",
                                url=current_url,
                                status_code=response.status_code,
                            )

                        content_type = response.headers.get("content-type", "")
                        content_type = content_type.split(";", 1)[0].strip().lower()
                        if content_type not in accepted:
                            raise BlenderDocumentationRetrievalError(
                                "invalid_content_type",
                                f"Unexpected documentation content type: {content_type or 'missing'}",
                                url=current_url,
                            )

                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > max_bytes:
                                    raise BlenderDocumentationRetrievalError(
                                        "response_too_large",
                                        f"Documentation response exceeds {max_bytes} bytes",
                                        url=current_url,
                                    )
                            except ValueError:
                                pass

                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise BlenderDocumentationRetrievalError(
                                    "response_too_large",
                                    f"Documentation response exceeds {max_bytes} bytes after decoding",
                                    url=current_url,
                                )
                            chunks.append(chunk)
                        return FetchedDocument(
                            requested_url=requested_url,
                            url=current_url,
                            status_code=response.status_code,
                            content_type=content_type,
                            content=b"".join(chunks),
                            redirects=tuple(redirects),
                            etag=response.headers.get("etag"),
                            last_modified=response.headers.get("last-modified"),
                            cache={"status": "network"},
                        )
                except BlenderDocumentationRetrievalError:
                    raise
                except httpx.TimeoutException as exc:
                    raise BlenderDocumentationRetrievalError(
                        "timeout",
                        "Official documentation request timed out",
                        url=current_url,
                    ) from exc
                except httpx.HTTPError as exc:
                    raise BlenderDocumentationRetrievalError(
                        "network_error",
                        f"Official documentation request failed: {type(exc).__name__}",
                        url=current_url,
                    ) from exc

        raise BlenderDocumentationRetrievalError(
            "request_failed",
            "Official documentation request did not produce a response",
            url=current_url,
        )


def normalize_page_identifier(page: str, source: str) -> str:
    """Normalize a relative documentation page and reject URL/path injection."""

    raw = str(page or "").strip()
    if not raw or len(raw) > MAX_PAGE_IDENTIFIER_CHARS:
        raise BlenderDocumentationRetrievalError(
            "invalid_page",
            f"page must contain 1-{MAX_PAGE_IDENTIFIER_CHARS} characters",
        )
    decoded = raw
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if any(token in decoded for token in ("\\", "?", "#", "\x00")):
        raise BlenderDocumentationRetrievalError(
            "invalid_page",
            "page must not contain a query, fragment, backslash, or NUL",
        )
    if decoded.startswith(("/", "//")) or "://" in decoded:
        raise BlenderDocumentationRetrievalError(
            "invalid_page",
            "page must be a relative documentation identifier, not a URL",
        )
    parts = decoded.split("/")
    if any(part in {"", ".", ".."} for part in parts[:-1]) or ".." in parts:
        raise BlenderDocumentationRetrievalError(
            "invalid_page",
            "page must not contain empty, current, or parent path segments",
        )
    pattern = SAFE_API_PAGE_RE if source == SOURCE_PYTHON_API else SAFE_PAGE_RE
    if pattern.fullmatch(decoded) is None:
        raise BlenderDocumentationRetrievalError(
            "invalid_page",
            "page contains unsupported characters",
        )

    normalized = decoded.lstrip("/")
    if source in {SOURCE_MANUAL, SOURCE_PYTHON_API}:
        if normalized.endswith("/"):
            normalized += "index.html"
        elif not normalized.endswith(".html"):
            normalized += ".html"
    elif source == SOURCE_RELEASE_NOTES:
        if normalized.endswith(".html"):
            pass
        elif not normalized.endswith("/"):
            normalized += "/"
    else:
        raise BlenderDocumentationRetrievalError(
            "invalid_source",
            f"Unsupported documentation source: {source}",
        )
    return normalized


def build_page_url(source_record: Mapping[str, Any], page: str) -> tuple[str, str]:
    source = str(source_record.get("source") or "")
    base_url = validate_official_url(str(source_record.get("base_url") or ""))
    normalized = normalize_page_identifier(page, source)
    url = validate_official_url(urljoin(base_url, normalized))
    if not url.startswith(base_url):
        raise BlenderDocumentationRetrievalError(
            "invalid_page",
            "Resolved page escaped its documentation source root",
        )
    return normalized, url


def _english_manual_source(source_record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the same Manual channel rooted at English, when applicable."""

    if (
        source_record.get("source") != SOURCE_MANUAL
        or source_record.get("language") == "en"
    ):
        return None
    channel = str(source_record.get("channel") or "")
    if not channel:
        return None
    fallback = dict(source_record)
    fallback["language"] = "en"
    fallback["base_url"] = validate_official_url(
        f"https://docs.blender.org/manual/en/{channel}/"
    )
    return fallback


def _language_fallback(
    requested: str | None,
    resolved: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "used": bool(reason),
        "requested": requested,
        "resolved": resolved,
        "reason": reason,
    }
