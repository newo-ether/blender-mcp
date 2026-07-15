"""Bounded retrieval and extraction for official Blender documentation."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping
from urllib.parse import urljoin

from .constants import (
    DEFAULT_PAGE_MAX_CHARS,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SNIPPET_MODE,
    DEFAULT_SNIPPET_TOP_COUNT,
    DOCUMENTATION_PAGE_SCHEMA,
    DOCUMENTATION_SEARCH_SCHEMA,
    INDEX_CONTENT_TYPES,
    MAX_PAGE_BYTES,
    MAX_SEARCH_INDEX_BYTES,
    MAX_SEARCH_LIMIT,
    MAX_SNIPPET_WORKERS,
    PAGE_CONTENT_TYPES,
    SNIPPET_MODES,
)
from .context import (
    DOCUMENTATION_CONTEXT_SCHEMA,
    SOURCE_MANUAL,
    SOURCE_PYTHON_API,
    SOURCE_RELEASE_NOTES,
)
from .html import extract_html_page
from .http import (
    BlenderDocumentationRetrievalError,
    FetchedDocument,
    _english_manual_source,
    _language_fallback,
    build_page_url,
    validate_official_url,
)
from .search import (
    _compact_context,
    _parse_sphinx_index,
    _rank_mkdocs_index,
    _rank_sphinx_index,
    _snippet,
    _validate_query,
)


class BlenderDocumentationClient:
    """Search and fetch pages from source records produced by M1."""

    def __init__(
        self,
        fetcher: Callable[..., FetchedDocument] | None = None,
    ) -> None:
        if fetcher is None:
            from .cache import CachingOfficialDocsFetcher

            fetcher = CachingOfficialDocsFetcher()
        self.fetcher = fetcher

    def _retrieval_marker(self) -> int | None:
        events = getattr(self.fetcher, "events", None)
        return len(events) if isinstance(events, list) else None

    def _retrieval_events(self, marker: int | None) -> list[dict[str, Any]]:
        events = getattr(self.fetcher, "events", None)
        if marker is None or not isinstance(events, list):
            return []
        return [dict(item) for item in events[marker:]]

    @staticmethod
    def _validate_context(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if (
            not isinstance(context, Mapping)
            or context.get("schema") != DOCUMENTATION_CONTEXT_SCHEMA
        ):
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
        retrieval_marker = self._retrieval_marker()
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
                accepted_content_types=PAGE_CONTENT_TYPES,
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
                accepted_content_types=PAGE_CONTENT_TYPES,
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
            "cache": dict(fetched.cache or {"status": "unmanaged"}),
            "retrieval": self._retrieval_events(retrieval_marker),
            **extracted,
        }

    def _search_sphinx(
        self,
        source_record: Mapping[str, Any],
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        index_url = validate_official_url(
            urljoin(source_record["base_url"], "searchindex.js")
        )
        effective_source = source_record
        index_language_fallback = _language_fallback(
            source_record.get("language"),
            source_record.get("language"),
        )
        try:
            fetched = self.fetcher(
                index_url,
                accepted_content_types=INDEX_CONTENT_TYPES,
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
                accepted_content_types=INDEX_CONTENT_TYPES,
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
                "index_cache": dict(fetched.cache or {"status": "unmanaged"}),
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
            accepted_content_types=INDEX_CONTENT_TYPES,
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
            results.append(
                {
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
                    "index_cache": dict(fetched.cache or {"status": "unmanaged"}),
                }
            )
        return results

    def search(
        self,
        context: Mapping[str, Any],
        *,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        snippet_mode: str = DEFAULT_SNIPPET_MODE,
    ) -> dict[str, Any]:
        retrieval_marker = self._retrieval_marker()
        normalized_query = _validate_query(query)
        if not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise BlenderDocumentationRetrievalError(
                "invalid_limit",
                f"limit must be an integer from 1 to {MAX_SEARCH_LIMIT}",
            )
        if snippet_mode not in SNIPPET_MODES:
            raise BlenderDocumentationRetrievalError(
                "invalid_snippet_mode",
                "snippet_mode must be none, top, or all",
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
        deduplicated = []
        seen = set()
        for result in results:
            identity = (
                result["source"],
                str(result["path"]).casefold(),
                str(result["heading"] or "").casefold(),
            )
            if identity in seen:
                continue
            seen.add(identity)
            deduplicated.append(result)
        results = deduplicated[:limit]
        enrich_budget = {
            "none": 0,
            "top": DEFAULT_SNIPPET_TOP_COUNT,
            "all": len(results),
        }[snippet_mode]
        enrich_indexes = []
        for index, result in enumerate(results):
            needs_snippet = result.pop("_needs_snippet", False)
            if needs_snippet and index < enrich_budget:
                enrich_indexes.append(index)
            elif needs_snippet:
                result.pop("_english_page_url", None)
                result["snippet_deferred"] = True
            else:
                result.pop("_english_page_url", None)

        def enrich(index):
            result = results[index]
            english_page_url = result.pop("_english_page_url", None)
            try:
                page_response = self.fetcher(
                    result["url"],
                    accepted_content_types=PAGE_CONTENT_TYPES,
                    max_bytes=MAX_PAGE_BYTES,
                )
                extracted = extract_html_page(page_response.content, max_chars=2_000)
                result["snippet"] = _snippet(extracted["content"], normalized_query)
                result["url"] = page_response.url
                result["snippet_cache"] = dict(
                    page_response.cache or {"status": "unmanaged"}
                )
            except BlenderDocumentationRetrievalError as exc:
                if exc.code == "http_error" and english_page_url:
                    try:
                        page_response = self.fetcher(
                            english_page_url,
                            accepted_content_types=PAGE_CONTENT_TYPES,
                            max_bytes=MAX_PAGE_BYTES,
                        )
                        extracted = extract_html_page(
                            page_response.content, max_chars=2_000
                        )
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
                        result["snippet_cache"] = dict(
                            page_response.cache or {"status": "unmanaged"}
                        )
                    except BlenderDocumentationRetrievalError as fallback_exc:
                        result["snippet_error"] = fallback_exc.code
                else:
                    result["snippet_error"] = exc.code
            return index

        if enrich_indexes:
            with ThreadPoolExecutor(
                max_workers=min(MAX_SNIPPET_WORKERS, len(enrich_indexes))
            ) as executor:
                list(executor.map(enrich, enrich_indexes))
        return {
            "schema": DOCUMENTATION_SEARCH_SCHEMA,
            "documentation_context": _compact_context(context),
            "query": normalized_query,
            "limit": limit,
            "snippet_mode": snippet_mode,
            "snippet_enriched_count": len(enrich_indexes),
            "result_count": len(results),
            "results": results,
            "errors": errors,
            "retrieval": self._retrieval_events(retrieval_marker),
        }
