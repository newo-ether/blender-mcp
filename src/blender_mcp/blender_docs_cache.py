"""Transparent, version-isolated cache for official Blender documentation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping

from .blender_docs_retrieval import (
    BlenderDocumentationRetrievalError,
    FetchedDocument,
    OfficialDocsFetcher,
    validate_official_url,
)


CACHE_SCHEMA = "blender-documentation-cache/1"
DEFAULT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_FUTURE_CLOCK_SKEW_SECONDS = 5 * 60


def default_documentation_cache_root() -> Path:
    override = os.getenv("BLENDER_MCP_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve() / "docs-v1"
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA")
        if base:
            return Path(base) / "BlenderMCP" / "Cache" / "docs-v1"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "blender-mcp" / "docs-v1"
    base = os.getenv("XDG_CACHE_HOME")
    if base:
        return Path(base) / "blender-mcp" / "docs-v1"
    return Path.home() / ".cache" / "blender-mcp" / "docs-v1"


class CachingOfficialDocsFetcher:
    """Wrap an official fetcher with atomic disk cache and stale fallback."""

    def __init__(
        self,
        base_fetcher: Callable[..., FetchedDocument] | None = None,
        *,
        cache_root: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES,
        allow_stale: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(ttl_seconds, int) or ttl_seconds < 0:
            raise ValueError("ttl_seconds must be a non-negative integer")
        if not isinstance(max_cache_bytes, int) or max_cache_bytes < 1:
            raise ValueError("max_cache_bytes must be a positive integer")
        self.base_fetcher = base_fetcher or OfficialDocsFetcher()
        self.cache_root = Path(cache_root) if cache_root else default_documentation_cache_root()
        self.ttl_seconds = ttl_seconds
        self.max_cache_bytes = max_cache_bytes
        self.allow_stale = bool(allow_stale)
        self.clock = clock
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(
            f"{CACHE_SCHEMA}\0{url}".encode("utf-8")
        ).hexdigest()

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = self._key(url)
        return self.cache_root / f"{key}.json", self.cache_root / f"{key}.bin"

    def _ensure_root(self) -> bool:
        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            return self.cache_root.is_dir()
        except OSError:
            return False

    @staticmethod
    def _remove_files(*paths: Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_entry(
        self,
        url: str,
        *,
        accepted_content_types: set[str],
        max_bytes: int,
    ) -> tuple[dict[str, Any], bytes] | None:
        metadata_path, content_path = self._paths(url)
        if not metadata_path.is_file() or not content_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (
                not isinstance(metadata, dict)
                or metadata.get("schema") != CACHE_SCHEMA
                or metadata.get("requested_url") != url
            ):
                raise ValueError("cache metadata identity mismatch")
            final_url = validate_official_url(str(metadata["url"]))
            content_type = str(metadata["content_type"]).lower()
            if content_type not in accepted_content_types:
                raise ValueError("cached content type is no longer accepted")
            fetched_at = float(metadata["fetched_at"])
            if fetched_at > self.clock() + MAX_FUTURE_CLOCK_SKEW_SECONDS:
                raise ValueError("cache timestamp is too far in the future")
            content = content_path.read_bytes()
            if len(content) > max_bytes:
                raise ValueError("cached response exceeds the current limit")
            digest = hashlib.sha256(content).hexdigest()
            if digest != metadata.get("content_sha256"):
                raise ValueError("cached content hash mismatch")
            metadata["url"] = final_url
            return metadata, content
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._remove_files(metadata_path, content_path)
            return None

    def _atomic_write(self, path: Path, content: bytes) -> None:
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=path.name + ".",
            suffix=".tmp",
            dir=self.cache_root,
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _write_entry(self, document: FetchedDocument, fetched_at: float) -> bool:
        if not self._ensure_root():
            return False
        metadata_path, content_path = self._paths(document.requested_url)
        metadata = {
            "schema": CACHE_SCHEMA,
            "requested_url": document.requested_url,
            "url": document.url,
            "content_type": document.content_type,
            "content_sha256": hashlib.sha256(document.content).hexdigest(),
            "content_bytes": len(document.content),
            "fetched_at": fetched_at,
            "etag": document.etag,
            "last_modified": document.last_modified,
            "redirects": list(document.redirects),
        }
        try:
            self._atomic_write(content_path, document.content)
            self._atomic_write(
                metadata_path,
                (json.dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n").encode("utf-8"),
            )
            self._prune()
            return True
        except OSError:
            self._remove_files(metadata_path, content_path)
            return False

    def _refresh_metadata(self, metadata: dict[str, Any], fetched_at: float) -> bool:
        metadata = dict(metadata)
        metadata["fetched_at"] = fetched_at
        metadata_path, _ = self._paths(metadata["requested_url"])
        try:
            self._atomic_write(
                metadata_path,
                (json.dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n").encode("utf-8"),
            )
            return True
        except OSError:
            return False

    def _prune(self) -> None:
        try:
            entries = []
            total = 0
            for metadata_path in self.cache_root.glob("*.json"):
                content_path = metadata_path.with_suffix(".bin")
                if not content_path.is_file():
                    self._remove_files(metadata_path)
                    continue
                try:
                    size = metadata_path.stat().st_size + content_path.stat().st_size
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    fetched_at = float(metadata.get("fetched_at", 0))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    self._remove_files(metadata_path, content_path)
                    continue
                total += size
                entries.append((fetched_at, size, metadata_path, content_path))
            if total <= self.max_cache_bytes:
                return
            for _fetched_at, size, metadata_path, content_path in sorted(entries):
                self._remove_files(metadata_path, content_path)
                total -= size
                if total <= self.max_cache_bytes:
                    break
        except OSError:
            return

    def _cached_document(
        self,
        metadata: Mapping[str, Any],
        content: bytes,
        *,
        status: str,
        stale: bool,
        age_seconds: float,
        error: BlenderDocumentationRetrievalError | None = None,
    ) -> FetchedDocument:
        cache = {
            "status": status,
            "stale": stale,
            "age_seconds": round(max(0.0, age_seconds), 3),
            "fetched_at": metadata["fetched_at"],
        }
        if error is not None:
            cache["fallback_error"] = error.code
        event = {"url": metadata["requested_url"], **cache}
        self.events.append(event)
        return FetchedDocument(
            requested_url=metadata["requested_url"],
            url=metadata["url"],
            status_code=200,
            content_type=metadata["content_type"],
            content=content,
            redirects=tuple(metadata.get("redirects", [])),
            etag=metadata.get("etag"),
            last_modified=metadata.get("last_modified"),
            cache=cache,
        )

    def _network_document(
        self,
        document: FetchedDocument,
        *,
        status: str,
        fetched_at: float,
        cache_written: bool,
    ) -> FetchedDocument:
        cache = {
            "status": status if cache_written else "cache_unavailable",
            "stale": False,
            "age_seconds": 0.0,
            "fetched_at": fetched_at,
        }
        self.events.append({"url": document.requested_url, **cache})
        return FetchedDocument(
            requested_url=document.requested_url,
            url=document.url,
            status_code=document.status_code,
            content_type=document.content_type,
            content=document.content,
            redirects=document.redirects,
            etag=document.etag,
            last_modified=document.last_modified,
            cache=cache,
        )

    def __call__(
        self,
        url: str,
        *,
        accepted_content_types: Iterable[str],
        max_bytes: int,
    ) -> FetchedDocument:
        requested_url = validate_official_url(url)
        accepted = {str(item).lower() for item in accepted_content_types}
        now = self.clock()
        cache_available = self._ensure_root()
        entry = self._load_entry(
            requested_url,
            accepted_content_types=accepted,
            max_bytes=max_bytes,
        ) if cache_available else None

        if entry is not None:
            metadata, content = entry
            age = max(0.0, now - float(metadata["fetched_at"]))
            if age <= self.ttl_seconds:
                return self._cached_document(
                    metadata,
                    content,
                    status="hit",
                    stale=False,
                    age_seconds=age,
                )

            conditional_headers = {}
            if metadata.get("etag"):
                conditional_headers["If-None-Match"] = metadata["etag"]
            if metadata.get("last_modified"):
                conditional_headers["If-Modified-Since"] = metadata["last_modified"]
            try:
                refreshed = self.base_fetcher(
                    requested_url,
                    accepted_content_types=accepted,
                    max_bytes=max_bytes,
                    request_headers=conditional_headers,
                )
                if refreshed.status_code == 304:
                    metadata = dict(metadata)
                    metadata["fetched_at"] = now
                    metadata["etag"] = refreshed.etag or metadata.get("etag")
                    metadata["last_modified"] = (
                        refreshed.last_modified or metadata.get("last_modified")
                    )
                    self._refresh_metadata(metadata, now)
                    return self._cached_document(
                        metadata,
                        content,
                        status="revalidated",
                        stale=False,
                        age_seconds=0.0,
                    )
                written = self._write_entry(refreshed, now)
                return self._network_document(
                    refreshed,
                    status="refreshed",
                    fetched_at=now,
                    cache_written=written,
                )
            except BlenderDocumentationRetrievalError as exc:
                transient_http = (
                    exc.code == "http_error"
                    and exc.status_code is not None
                    and exc.status_code >= 500
                )
                if self.allow_stale and (
                    exc.code in {"timeout", "network_error"} or transient_http
                ):
                    return self._cached_document(
                        metadata,
                        content,
                        status="stale_fallback",
                        stale=True,
                        age_seconds=age,
                        error=exc,
                    )
                raise

        document = self.base_fetcher(
            requested_url,
            accepted_content_types=accepted,
            max_bytes=max_bytes,
            request_headers={},
        )
        if document.status_code == 304:
            raise BlenderDocumentationRetrievalError(
                "invalid_not_modified",
                "Official documentation returned 304 without a cache entry",
                url=requested_url,
                status_code=304,
            )
        written = self._write_entry(document, now) if cache_available else False
        return self._network_document(
            document,
            status="miss",
            fetched_at=now,
            cache_written=written,
        )
