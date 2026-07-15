"""Compatibility facade for official documentation retrieval."""

from .client import BlenderDocumentationClient
from .html import extract_html_page
from .http import (
    BlenderDocumentationRetrievalError,
    FetchedDocument,
    OfficialDocsFetcher,
    build_page_url,
    normalize_page_identifier,
    validate_official_url,
)

__all__ = [
    "BlenderDocumentationClient",
    "BlenderDocumentationRetrievalError",
    "FetchedDocument",
    "OfficialDocsFetcher",
    "build_page_url",
    "extract_html_page",
    "normalize_page_identifier",
    "validate_official_url",
]
