"""Bounded retrieval and extraction for official Blender documentation."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote, urljoin, urlparse

import httpx

from .blender_docs import (
    DOCUMENTATION_CONTEXT_SCHEMA,
    OFFICIAL_DOCUMENTATION_HOSTS,
    SOURCE_MANUAL,
    SOURCE_PYTHON_API,
    SOURCE_RELEASE_NOTES,
)


DOCUMENTATION_SEARCH_SCHEMA = "blender-documentation-search/1"
DOCUMENTATION_PAGE_SCHEMA = "blender-documentation-page/1"

DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 20
DEFAULT_PAGE_MAX_CHARS = 12_000
MAX_PAGE_MAX_CHARS = 50_000
MAX_QUERY_CHARS = 200
MAX_PAGE_IDENTIFIER_CHARS = 500
MAX_SEARCH_INDEX_BYTES = 12 * 1024 * 1024
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 3

_INDEX_CONTENT_TYPES = frozenset({
    "application/javascript",
    "application/json",
    "application/x-javascript",
    "text/javascript",
    "text/plain",
})
_PAGE_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_SAFE_PAGE_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_SAFE_API_PAGE_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_WORD_RE = re.compile(r"[^\W_]+(?:[-_.][^\W_]+)*", re.UNICODE)


class BlenderDocumentationRetrievalError(RuntimeError):
    """Raised when an official documentation request cannot be completed."""

    def __init__(self, code: str, message: str, *, url: str | None = None):
        super().__init__(message)
        self.code = code
        self.url = url

    def as_dict(self, *, source: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if source is not None:
            result["source"] = source
        if self.url is not None:
            result["url"] = self.url
        return result


@dataclass(frozen=True)
class FetchedDocument:
    requested_url: str
    url: str
    status_code: int
    content_type: str
    content: bytes
    redirects: tuple[str, ...]


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
    ) -> FetchedDocument:
        requested_url = validate_official_url(url)
        current_url = requested_url
        redirects: list[str] = []
        accepted = frozenset(item.lower() for item in accepted_content_types)
        headers = {
            "Accept": ", ".join(sorted(accepted)),
            "User-Agent": "blender-mcp-documentation/1",
        }

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
                            target = validate_official_url(urljoin(current_url, location))
                            redirects.append(target)
                            current_url = target
                            continue

                        if response.status_code != 200:
                            raise BlenderDocumentationRetrievalError(
                                "http_error",
                                f"Official documentation returned HTTP {response.status_code}",
                                url=current_url,
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
    pattern = _SAFE_API_PAGE_RE if source == SOURCE_PYTHON_API else _SAFE_PAGE_RE
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

    if source_record.get("source") != SOURCE_MANUAL or source_record.get("language") == "en":
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


class _DocumentationHTMLParser(HTMLParser):
    _SKIP_TAGS = frozenset({"script", "style", "nav", "header", "footer", "svg", "form"})
    _SKIP_CLASSES = frozenset({"anchor", "header-anchor", "headerlink"})
    _VOID_TAGS = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })
    _BLOCK_TAGS = frozenset({
        "address", "blockquote", "dd", "div", "dl", "dt", "figcaption",
        "figure", "li", "p", "pre", "table", "td", "th", "tr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.body_blocks: list[dict[str, Any]] = []
        self.main_blocks: list[dict[str, Any]] = []
        self._skip_depth = 0
        self._main_depth = 0
        self._in_title = False
        self._current_kind: str | None = None
        self._current_level: int | None = None
        self._current_parts: list[str] = []

    @staticmethod
    def _is_main(attrs: list[tuple[str, str | None]]) -> bool:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        return (
            values.get("role") == "main"
            or "document" in classes
            or "body" in classes
            or "page-content" in classes
        )

    def _flush(self) -> None:
        if self._current_kind is None:
            return
        text = " ".join("".join(self._current_parts).split())
        if text:
            block = {"kind": self._current_kind, "text": text}
            if self._current_level is not None:
                block["level"] = self._current_level
            self.body_blocks.append(block)
            if self._main_depth > 0:
                self.main_blocks.append(dict(block))
        self._current_kind = None
        self._current_level = None
        self._current_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag not in self._VOID_TAGS:
                self._skip_depth += 1
            return
        classes = set((dict(attrs).get("class") or "").split())
        if tag in self._SKIP_TAGS or classes.intersection(self._SKIP_CLASSES):
            self._flush()
            self._skip_depth = 1
            return
        if tag == "title":
            self._in_title = True
        if tag in {"main", "article"} or self._is_main(attrs):
            self._main_depth += 1
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._current_kind = "heading"
            self._current_level = int(tag[1])
        elif tag in self._BLOCK_TAGS:
            self._flush()
            self._current_kind = "text"
        elif tag == "br" and self._current_kind is not None:
            self._current_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if re.fullmatch(r"h[1-6]", tag) or tag in self._BLOCK_TAGS:
            self._flush()
        if tag in {"main", "article"}:
            self._main_depth = max(0, self._main_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._current_kind is not None:
            self._current_parts.append(data)

    def close(self) -> None:
        self._flush()
        super().close()


def extract_html_page(
    content: bytes,
    *,
    heading: str | None = None,
    max_chars: int = DEFAULT_PAGE_MAX_CHARS,
) -> dict[str, Any]:
    """Extract readable blocks and an optional heading-bounded section."""

    if not isinstance(max_chars, int) or not 100 <= max_chars <= MAX_PAGE_MAX_CHARS:
        raise BlenderDocumentationRetrievalError(
            "invalid_max_chars",
            f"max_chars must be an integer from 100 to {MAX_PAGE_MAX_CHARS}",
        )
    parser = _DocumentationHTMLParser()
    try:
        parser.feed(content.decode("utf-8", errors="replace"))
        parser.close()
    except Exception as exc:
        raise BlenderDocumentationRetrievalError(
            "malformed_html",
            "Unable to parse official documentation HTML",
        ) from exc

    blocks = parser.main_blocks or parser.body_blocks
    headings = [
        {"level": block["level"], "text": block["text"]}
        for block in blocks
        if block["kind"] == "heading"
    ]
    selected_heading = None
    if heading:
        wanted = " ".join(str(heading).split()).casefold()
        start = None
        start_level = None
        for index, block in enumerate(blocks):
            if block["kind"] == "heading" and block["text"].casefold() == wanted:
                start = index
                start_level = block["level"]
                selected_heading = block["text"]
                break
        if start is None:
            raise BlenderDocumentationRetrievalError(
                "heading_not_found",
                f"Heading not found: {heading}",
            )
        end = len(blocks)
        for index in range(start + 1, len(blocks)):
            block = blocks[index]
            if block["kind"] == "heading" and block["level"] <= start_level:
                end = index
                break
        blocks = blocks[start:end]

    lines = [block["text"] for block in blocks if block["text"]]
    text = "\n\n".join(lines).strip()
    if not text:
        raise BlenderDocumentationRetrievalError(
            "empty_page",
            "Official documentation page contained no readable content",
        )
    truncated = len(text) > max_chars
    if truncated:
        text = text[: max_chars - 1].rstrip() + "…"
    title = " ".join("".join(parser.title_parts).split())
    if not title and headings:
        title = headings[0]["text"]
    return {
        "title": title,
        "heading": selected_heading,
        "content": text,
        "characters": len(text),
        "truncated": truncated,
        "headings": headings[:100],
    }


def _parse_sphinx_index(content: bytes) -> Mapping[str, Any]:
    try:
        text = content.decode("utf-8", errors="strict").strip()
        match = re.fullmatch(r"Search\.setIndex\((.*)\);?", text, flags=re.DOTALL)
        if match:
            text = match.group(1)
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlenderDocumentationRetrievalError(
            "malformed_search_index",
            "Official Sphinx search index is malformed",
        ) from exc
    if not isinstance(parsed, Mapping):
        raise BlenderDocumentationRetrievalError(
            "malformed_search_index",
            "Official Sphinx search index has an invalid root",
        )
    return parsed


def _posting_ids(value: Any) -> set[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, int) and not isinstance(item, bool)}
    if isinstance(value, Mapping):
        ids: set[int] = set()
        for key, nested in value.items():
            if str(key).isdigit():
                ids.add(int(key))
            ids.update(_posting_ids(nested))
        return ids
    return set()


def _query_tokens(query: str) -> list[str]:
    return [match.group(0).casefold() for match in _WORD_RE.finditer(query)]


def _validate_query(query: str) -> str:
    raw = str(query or "")
    if any(ord(character) < 32 for character in raw):
        raise BlenderDocumentationRetrievalError(
            "invalid_query",
            "query contains control characters",
        )
    normalized = " ".join(raw.split())
    if not normalized or len(normalized) > MAX_QUERY_CHARS:
        raise BlenderDocumentationRetrievalError(
            "invalid_query",
            f"query must contain 1-{MAX_QUERY_CHARS} characters",
        )
    return normalized


def _snippet(text: str, query: str, limit: int = 280) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    position = clean.casefold().find(query.casefold())
    if position < 0:
        position = 0
    start = max(0, position - limit // 3)
    end = min(len(clean), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix


def _rank_sphinx_index(index: Mapping[str, Any], query: str) -> list[dict[str, Any]]:
    docnames = index.get("docnames")
    titles = index.get("titles")
    if not isinstance(docnames, list) or not isinstance(titles, list):
        raise BlenderDocumentationRetrievalError(
            "malformed_search_index",
            "Sphinx search index is missing docnames or titles",
        )
    count = min(len(docnames), len(titles))
    scores = [0] * count
    folded = query.casefold()
    tokens = _query_tokens(query)
    for index_id in range(count):
        title = str(titles[index_id])
        path = str(docnames[index_id])
        title_folded = title.casefold()
        path_folded = path.casefold()
        if title_folded == folded:
            scores[index_id] += 300
        elif folded in title_folded:
            scores[index_id] += 160
        if folded in path_folded:
            scores[index_id] += 80
        for token in tokens:
            if token in title_folded:
                scores[index_id] += 35
            if token in path_folded:
                scores[index_id] += 15

    for field, weight in (("titleterms", 55), ("terms", 20)):
        terms = index.get(field)
        if not isinstance(terms, Mapping):
            continue
        for term, postings in terms.items():
            term_folded = str(term).casefold()
            matches = sum(
                1 for token in tokens
                if token == term_folded or term_folded.startswith(token)
            )
            if not matches:
                continue
            for index_id in _posting_ids(postings):
                if 0 <= index_id < count:
                    scores[index_id] += weight * matches

    ranked = []
    for index_id, score in enumerate(scores):
        if score <= 0:
            continue
        ranked.append({
            "score": score,
            "title": str(titles[index_id]),
            "path": str(docnames[index_id]),
        })
    ranked.sort(key=lambda item: (-item["score"], item["title"].casefold(), item["path"]))
    return ranked


def _rank_mkdocs_index(
    index: Mapping[str, Any],
    query: str,
    version: str | None,
) -> list[dict[str, Any]]:
    documents = index.get("docs")
    if not isinstance(documents, list):
        raise BlenderDocumentationRetrievalError(
            "malformed_search_index",
            "MkDocs search index is missing docs",
        )
    prefix = f"release_notes/{version}/" if version else "release_notes/"
    folded = query.casefold()
    tokens = _query_tokens(query)
    ranked: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        location = str(document.get("location") or "").lstrip("/")
        if not location.startswith(prefix):
            continue
        title = str(document.get("title") or location)
        text = str(document.get("text") or "")
        haystack = f"{title} {location} {text}".casefold()
        score = 0
        if title.casefold() == folded:
            score += 300
        elif folded in title.casefold():
            score += 160
        if folded in text.casefold():
            score += 90
        if folded in location.casefold():
            score += 70
        score += sum(15 for token in tokens if token in haystack)
        if score:
            ranked.append({
                "score": score,
                "title": title,
                "path": location[len(prefix):].split("#", 1)[0] or "index",
                "heading": title if "#" in location else None,
                "snippet": _snippet(text, query),
            })
    ranked.sort(key=lambda item: (-item["score"], item["title"].casefold(), item["path"]))
    return ranked


def _compact_context(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": context.get("schema"),
        "requested": context.get("requested"),
        "detected_blender": context.get("detected_blender"),
        "resolved": context.get("resolved"),
        "warnings": context.get("warnings", []),
    }


class BlenderDocumentationClient:
    """Search and fetch pages from source records produced by M1."""

    def __init__(
        self,
        fetcher: Callable[..., FetchedDocument] | None = None,
    ) -> None:
        self.fetcher = fetcher or OfficialDocsFetcher()

    @staticmethod
    def _validate_context(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if not isinstance(context, Mapping) or context.get("schema") != DOCUMENTATION_CONTEXT_SCHEMA:
            raise BlenderDocumentationRetrievalError(
                "invalid_context",
                "Expected a blender-documentation-context/1 object",
            )
        sources = context.get("sources")
        if not isinstance(sources, list) or not sources:
            raise BlenderDocumentationRetrievalError(
                "invalid_context",
                "Documentation context contains no sources",
            )
        return sources

    def get_page(
        self,
        context: Mapping[str, Any],
        *,
        page: str,
        source: str,
        heading: str | None = None,
        max_chars: int = DEFAULT_PAGE_MAX_CHARS,
    ) -> dict[str, Any]:
        sources = self._validate_context(context)
        source_record = next(
            (record for record in sources if record.get("source") == source),
            None,
        )
        if source_record is None:
            raise BlenderDocumentationRetrievalError(
                "invalid_source",
                f"Documentation context does not contain source: {source}",
            )
        normalized_page, url = build_page_url(source_record, page)
        effective_source = source_record
        language_fallback = _language_fallback(
            source_record.get("language"),
            source_record.get("language"),
        )
        try:
            fetched = self.fetcher(
                url,
                accepted_content_types=_PAGE_CONTENT_TYPES,
                max_bytes=MAX_PAGE_BYTES,
            )
        except BlenderDocumentationRetrievalError as exc:
            fallback_source = _english_manual_source(source_record)
            if exc.code != "http_error" or fallback_source is None:
                raise
            effective_source = fallback_source
            normalized_page, url = build_page_url(fallback_source, page)
            fetched = self.fetcher(
                url,
                accepted_content_types=_PAGE_CONTENT_TYPES,
                max_bytes=MAX_PAGE_BYTES,
            )
            language_fallback = _language_fallback(
                source_record.get("language"),
                "en",
                "localized_page_unavailable_uses_english",
            )
        extracted = extract_html_page(
            fetched.content,
            heading=heading,
            max_chars=max_chars,
        )
        return {
            "schema": DOCUMENTATION_PAGE_SCHEMA,
            "documentation_context": _compact_context(context),
            "source": source,
            "source_version": effective_source.get("version"),
            "source_channel": effective_source.get("channel"),
            "language": effective_source.get("language"),
            "language_fallback": language_fallback,
            "requested_page": page,
            "page": normalized_page,
            "url": fetched.url,
            "redirects": list(fetched.redirects),
            **extracted,
        }

    def _search_sphinx(
        self,
        source_record: Mapping[str, Any],
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        index_url = validate_official_url(urljoin(source_record["base_url"], "searchindex.js"))
        effective_source = source_record
        index_language_fallback = _language_fallback(
            source_record.get("language"),
            source_record.get("language"),
        )
        try:
            fetched = self.fetcher(
                index_url,
                accepted_content_types=_INDEX_CONTENT_TYPES,
                max_bytes=MAX_SEARCH_INDEX_BYTES,
            )
        except BlenderDocumentationRetrievalError as exc:
            fallback_source = _english_manual_source(source_record)
            if exc.code != "http_error" or fallback_source is None:
                raise
            effective_source = fallback_source
            index_url = validate_official_url(
                urljoin(fallback_source["base_url"], "searchindex.js")
            )
            fetched = self.fetcher(
                index_url,
                accepted_content_types=_INDEX_CONTENT_TYPES,
                max_bytes=MAX_SEARCH_INDEX_BYTES,
            )
            index_language_fallback = _language_fallback(
                source_record.get("language"),
                "en",
                "localized_index_unavailable_uses_english",
            )
        ranked = _rank_sphinx_index(_parse_sphinx_index(fetched.content), query)
        results: list[dict[str, Any]] = []
        for candidate in ranked[:limit]:
            page = candidate["path"]
            normalized_page, page_url = build_page_url(effective_source, page)
            english_page_url = None
            if effective_source is source_record:
                fallback_source = _english_manual_source(source_record)
                if fallback_source is not None:
                    _, english_page_url = build_page_url(fallback_source, page)
            result = {
                "source": effective_source["source"],
                "source_version": effective_source.get("version"),
                "source_channel": effective_source.get("channel"),
                "language": effective_source.get("language"),
                "language_fallback": dict(index_language_fallback),
                "title": candidate["title"],
                "path": normalized_page,
                "heading": None,
                "snippet": "",
                "url": page_url,
                "score": candidate["score"],
                "_needs_snippet": True,
                "_english_page_url": english_page_url,
            }
            results.append(result)
        return results

    def _search_release_notes(
        self,
        source_record: Mapping[str, Any],
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        index_url = "https://developer.blender.org/docs/search/search_index.json"
        fetched = self.fetcher(
            index_url,
            accepted_content_types=_INDEX_CONTENT_TYPES,
            max_bytes=MAX_SEARCH_INDEX_BYTES,
        )
        try:
            index = json.loads(fetched.content.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BlenderDocumentationRetrievalError(
                "malformed_search_index",
                "Official Release Notes search index is malformed",
                url=index_url,
            ) from exc
        ranked = _rank_mkdocs_index(index, query, source_record.get("version"))
        results = []
        for candidate in ranked[:limit]:
            normalized_page, url = build_page_url(source_record, candidate["path"])
            results.append({
                "source": SOURCE_RELEASE_NOTES,
                "source_version": source_record.get("version"),
                "source_channel": source_record.get("channel"),
                "language": "en",
                "language_fallback": _language_fallback("en", "en"),
                "title": candidate["title"],
                "path": normalized_page,
                "heading": candidate.get("heading"),
                "snippet": candidate.get("snippet", ""),
                "url": url,
                "score": candidate["score"],
            })
        return results

    def search(
        self,
        context: Mapping[str, Any],
        *,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> dict[str, Any]:
        normalized_query = _validate_query(query)
        if not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise BlenderDocumentationRetrievalError(
                "invalid_limit",
                f"limit must be an integer from 1 to {MAX_SEARCH_LIMIT}",
            )
        source_records = self._validate_context(context)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for source_record in source_records:
            source = source_record.get("source")
            try:
                if source in {SOURCE_MANUAL, SOURCE_PYTHON_API}:
                    source_results = self._search_sphinx(
                        source_record,
                        normalized_query,
                        limit,
                    )
                elif source == SOURCE_RELEASE_NOTES:
                    source_results = self._search_release_notes(
                        source_record,
                        normalized_query,
                        limit,
                    )
                else:
                    raise BlenderDocumentationRetrievalError(
                        "invalid_source",
                        f"Unsupported documentation source: {source}",
                    )
                results.extend(source_results)
            except BlenderDocumentationRetrievalError as exc:
                errors.append(exc.as_dict(source=str(source)))

        results.sort(
            key=lambda item: (
                -int(item["score"]),
                str(item["title"]).casefold(),
                str(item["source"]),
                str(item["path"]),
            )
        )
        results = results[:limit]
        for result in results:
            if not result.pop("_needs_snippet", False):
                result.pop("_english_page_url", None)
                continue
            english_page_url = result.pop("_english_page_url", None)
            try:
                page_response = self.fetcher(
                    result["url"],
                    accepted_content_types=_PAGE_CONTENT_TYPES,
                    max_bytes=MAX_PAGE_BYTES,
                )
                extracted = extract_html_page(page_response.content, max_chars=2_000)
                result["snippet"] = _snippet(extracted["content"], normalized_query)
                result["url"] = page_response.url
            except BlenderDocumentationRetrievalError as exc:
                if exc.code == "http_error" and english_page_url:
                    try:
                        page_response = self.fetcher(
                            english_page_url,
                            accepted_content_types=_PAGE_CONTENT_TYPES,
                            max_bytes=MAX_PAGE_BYTES,
                        )
                        extracted = extract_html_page(page_response.content, max_chars=2_000)
                        result["snippet"] = _snippet(
                            extracted["content"],
                            normalized_query,
                        )
                        result["url"] = page_response.url
                        result["language"] = "en"
                        result["language_fallback"] = _language_fallback(
                            result["language_fallback"].get("requested"),
                            "en",
                            "localized_page_unavailable_uses_english",
                        )
                    except BlenderDocumentationRetrievalError as fallback_exc:
                        result["snippet_error"] = fallback_exc.code
                else:
                    result["snippet_error"] = exc.code
        return {
            "schema": DOCUMENTATION_SEARCH_SCHEMA,
            "documentation_context": _compact_context(context),
            "query": normalized_query,
            "limit": limit,
            "result_count": len(results),
            "results": results,
            "errors": errors,
        }
