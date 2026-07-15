"""Bounded retrieval and extraction for official Blender documentation."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .constants import MAX_QUERY_CHARS, WORD_RE
from .http import BlenderDocumentationRetrievalError


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
        return {
            item
            for item in value
            if isinstance(item, int) and not isinstance(item, bool)
        }
    if isinstance(value, Mapping):
        ids: set[int] = set()
        for key, nested in value.items():
            if str(key).isdigit():
                ids.add(int(key))
            ids.update(_posting_ids(nested))
        return ids
    return set()


def _query_tokens(query: str) -> list[str]:
    return [match.group(0).casefold() for match in WORD_RE.finditer(query)]


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
                1
                for token in tokens
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
        ranked.append(
            {
                "score": score,
                "title": str(titles[index_id]),
                "path": str(docnames[index_id]),
            }
        )
    ranked.sort(
        key=lambda item: (-item["score"], item["title"].casefold(), item["path"])
    )
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
            ranked.append(
                {
                    "score": score,
                    "title": title,
                    "path": location[len(prefix) :].split("#", 1)[0] or "index",
                    "heading": title if "#" in location else None,
                    "snippet": _snippet(text, query),
                }
            )
    ranked.sort(
        key=lambda item: (-item["score"], item["title"].casefold(), item["path"])
    )
    return ranked


def _compact_context(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": context.get("schema"),
        "requested": context.get("requested"),
        "detected_blender": context.get("detected_blender"),
        "resolved": context.get("resolved"),
        "warnings": context.get("warnings", []),
    }
