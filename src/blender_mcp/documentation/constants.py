"""Shared bounds and schemas for official documentation retrieval."""

import re

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
SNIPPET_MODES = frozenset({"none", "top", "all"})
DEFAULT_SNIPPET_MODE = "top"
DEFAULT_SNIPPET_TOP_COUNT = 3
MAX_SNIPPET_WORKERS = 3

INDEX_CONTENT_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/x-javascript",
        "text/javascript",
        "text/plain",
    }
)
PAGE_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
SAFE_PAGE_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
SAFE_API_PAGE_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
WORD_RE = re.compile(r"[^\W_]+(?:[-_.][^\W_]+)*", re.UNICODE)
