"""Version and official-source resolution for Blender documentation tools.

This module intentionally performs no network access.  It converts either a
connected Blender build record or an explicit version override into stable,
auditable source metadata that later retrieval tools can consume.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


DOCUMENTATION_CONTEXT_SCHEMA = "blender-documentation-context/1"

OFFICIAL_DOCUMENTATION_HOSTS = frozenset({
    "docs.blender.org",
    "developer.blender.org",
})

SOURCE_MANUAL = "manual"
SOURCE_PYTHON_API = "python_api"
SOURCE_RELEASE_NOTES = "release_notes"
DEFAULT_SOURCES = (
    SOURCE_MANUAL,
    SOURCE_PYTHON_API,
    SOURCE_RELEASE_NOTES,
)

_SOURCE_ALIASES = {
    "manual": SOURCE_MANUAL,
    "python_api": SOURCE_PYTHON_API,
    "python-api": SOURCE_PYTHON_API,
    "api": SOURCE_PYTHON_API,
    "release_notes": SOURCE_RELEASE_NOTES,
    "release-notes": SOURCE_RELEASE_NOTES,
    "releases": SOURCE_RELEASE_NOTES,
}

# Blender Manual language roots published by the official documentation
# project.  Availability can still differ by version/page; M2 will disclose a
# page-level English fallback when a localized page is absent.
SUPPORTED_MANUAL_LANGUAGES = frozenset({
    "ar",
    "ca",
    "cs",
    "de",
    "el",
    "en",
    "es",
    "fi",
    "fr",
    "id",
    "it",
    "ja",
    "ko",
    "nb",
    "nl",
    "pl",
    "pt",
    "pt-br",
    "ru",
    "sk",
    "sl",
    "sr",
    "sv",
    "tr",
    "uk",
    "vi",
    "zh-hans",
    "zh-hant",
})

_LANGUAGE_ALIASES = {
    "cn": "zh-hans",
    "en-us": "en",
    "en_us": "en",
    "pt_br": "pt-br",
    "zh": "zh-hans",
    "zh-cn": "zh-hans",
    "zh_cn": "zh-hans",
    "zh-hans-cn": "zh-hans",
    "zh-sg": "zh-hans",
    "zh_sg": "zh-hans",
    "zh-tw": "zh-hant",
    "zh_tw": "zh-hant",
    "zh-hk": "zh-hant",
    "zh_hk": "zh-hant",
}

_VERSION_RE = re.compile(
    r"^(?P<major>[1-9][0-9]*)\.(?P<minor>[0-9]+)(?:\.(?P<patch>[0-9]+))?$"
)
_PRERELEASE_CYCLES = frozenset({
    "alpha",
    "beta",
    "candidate",
    "prerelease",
    "rc",
})


class BlenderDocumentationContextError(ValueError):
    """Raised when a version, language, source, or build record is invalid."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def normalize_sources(sources: Iterable[str] | None) -> list[str]:
    """Normalize source aliases while preserving first-seen order."""

    requested = DEFAULT_SOURCES if sources is None else sources
    if isinstance(requested, str):
        requested = [requested]

    normalized: list[str] = []
    for raw in requested:
        key = _text(raw).strip().lower()
        source = _SOURCE_ALIASES.get(key)
        if source is None:
            choices = ", ".join(DEFAULT_SOURCES)
            raise BlenderDocumentationContextError(
                f"Unsupported Blender documentation source {raw!r}; expected: {choices}"
            )
        if source not in normalized:
            normalized.append(source)

    if not normalized:
        raise BlenderDocumentationContextError(
            "At least one Blender documentation source is required"
        )
    return normalized


def normalize_language(language: str | None) -> dict[str, Any]:
    """Normalize a requested Manual language without performing a lookup."""

    requested = _text(language or "en").strip().lower().replace("_", "-")
    if not requested:
        requested = "en"
    canonical = _LANGUAGE_ALIASES.get(requested, requested)
    supported = canonical in SUPPORTED_MANUAL_LANGUAGES
    return {
        "requested": requested,
        "normalized": canonical,
        "manual_language": canonical if supported else "en",
        "manual_language_supported": supported,
    }


def normalize_version_request(version: str | None) -> dict[str, Any]:
    """Parse auto/current/dev or an exact Blender major.minor[.patch]."""

    raw = _text(version or "auto").strip().lower()
    if not raw:
        raw = "auto"
    if raw in {"auto", "current", "dev"}:
        return {
            "requested": raw,
            "kind": raw,
            "version": None,
            "version_tuple": None,
        }

    match = _VERSION_RE.fullmatch(raw)
    if match is None:
        raise BlenderDocumentationContextError(
            "version must be 'auto', 'current', 'dev', or major.minor[.patch]"
        )
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch") or 0)
    return {
        "requested": raw,
        "kind": "exact",
        "version": f"{major}.{minor}",
        "version_tuple": [major, minor, patch],
    }


def version_requires_blender(version: str | None) -> bool:
    """Return whether resolving this request requires a live Blender build."""

    return normalize_version_request(version)["kind"] == "auto"


def normalize_detected_blender(build: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and stabilize the add-on's connected-build record."""

    if not isinstance(build, Mapping):
        raise BlenderDocumentationContextError(
            "version='auto' requires a connected Blender build context"
        )

    raw_version = build.get("version")
    version_tuple: list[int] | None = None
    if isinstance(raw_version, (list, tuple)) and len(raw_version) >= 2:
        try:
            values = [int(raw_version[0]), int(raw_version[1])]
            values.append(int(raw_version[2]) if len(raw_version) >= 3 else 0)
            if values[0] < 1 or values[1] < 0 or values[2] < 0:
                raise ValueError
            version_tuple = values
        except (TypeError, ValueError):
            version_tuple = None

    version_string = _text(build.get("version_string")).strip()
    if version_tuple is None and version_string:
        match = re.match(r"^([1-9][0-9]*)\.([0-9]+)(?:\.([0-9]+))?", version_string)
        if match:
            version_tuple = [
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3) or 0),
            ]
    if version_tuple is None:
        raise BlenderDocumentationContextError(
            "Connected Blender build did not provide a valid version"
        )

    cycle = _text(build.get("version_cycle") or "unknown").strip().lower()
    if cycle in {"stable", "final"}:
        cycle = "release"
    is_prerelease = bool(build.get("is_prerelease")) or cycle in _PRERELEASE_CYCLES

    raw_metadata = build.get("build")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    stable_build = {
        "branch": _text(metadata.get("branch")).strip() or None,
        "hash": _text(metadata.get("hash")).strip() or None,
        "date": _text(metadata.get("date")).strip() or None,
        "time": _text(metadata.get("time")).strip() or None,
        "platform": _text(metadata.get("platform")).strip() or None,
        "type": _text(metadata.get("type")).strip() or None,
        "commit_timestamp": metadata.get("commit_timestamp")
        if isinstance(metadata.get("commit_timestamp"), int)
        else None,
    }

    return {
        "version": version_tuple,
        "version_string": version_string or ".".join(map(str, version_tuple)),
        "version_cycle": cycle,
        "is_prerelease": is_prerelease,
        "is_lts": bool(build.get("is_lts")) or "LTS" in version_string.upper(),
        "build": stable_build,
    }


def _fallback(reasons: list[str]) -> dict[str, Any]:
    return {"used": bool(reasons), "reasons": reasons}


def _manual_source(
    request: Mapping[str, Any],
    effective_version: str | None,
    is_prerelease: bool | None,
    language: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    kind = request["kind"]
    if kind == "current":
        channel = "latest"
    elif kind == "dev":
        channel = "dev"
    elif kind == "auto" and is_prerelease:
        channel = "dev"
        reasons.append("detected_prerelease_uses_dev")
    else:
        channel = effective_version

    resolved_language = language["manual_language"]
    if not language["manual_language_supported"]:
        reasons.append("unsupported_language_uses_english")
    return {
        "source": SOURCE_MANUAL,
        "channel": channel,
        "version": effective_version,
        "language": resolved_language,
        "base_url": f"https://docs.blender.org/manual/{resolved_language}/{channel}/",
        "fallback": _fallback(reasons),
    }


def _python_api_source(
    request: Mapping[str, Any],
    effective_version: str | None,
    is_prerelease: bool | None,
    language: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    kind = request["kind"]
    if kind == "current":
        channel = "current"
    elif kind == "dev":
        channel = "dev"
    elif kind == "auto" and is_prerelease:
        channel = "dev"
        reasons.append("detected_prerelease_uses_dev")
    else:
        channel = effective_version
    if language["normalized"] != "en":
        reasons.append("source_is_english_only")
    return {
        "source": SOURCE_PYTHON_API,
        "channel": channel,
        "version": effective_version,
        "language": "en",
        "base_url": f"https://docs.blender.org/api/{channel}/",
        "fallback": _fallback(reasons),
    }


def _release_notes_source(
    request: Mapping[str, Any],
    effective_version: str | None,
    is_prerelease: bool | None,
    language: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if effective_version:
        channel = effective_version
        base_url = f"https://developer.blender.org/docs/release_notes/{channel}/"
    else:
        channel = "index"
        base_url = "https://developer.blender.org/docs/release_notes/"
        reasons.append("release_notes_require_numeric_version")
    if language["normalized"] != "en":
        reasons.append("source_is_english_only")
    return {
        "source": SOURCE_RELEASE_NOTES,
        "channel": channel,
        "version": effective_version,
        "language": "en",
        "base_url": base_url,
        "prerelease": is_prerelease,
        "fallback": _fallback(reasons),
    }


def resolve_documentation_context(
    *,
    version: str = "auto",
    language: str = "en",
    sources: Iterable[str] | None = None,
    detected_blender: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve Blender documentation roots without network access."""

    version_request = normalize_version_request(version)
    normalized_sources = normalize_sources(sources)
    language_context = normalize_language(language)

    detected = None
    effective_version: str | None
    release_cycle: str
    is_prerelease: bool | None
    if version_request["kind"] == "auto":
        detected = normalize_detected_blender(detected_blender)
        effective_version = f"{detected['version'][0]}.{detected['version'][1]}"
        release_cycle = detected["version_cycle"]
        is_prerelease = detected["is_prerelease"]
    elif version_request["kind"] == "exact":
        effective_version = version_request["version"]
        release_cycle = "explicit"
        is_prerelease = None
        if detected_blender is not None:
            detected = normalize_detected_blender(detected_blender)
    else:
        effective_version = None
        release_cycle = version_request["kind"]
        is_prerelease = version_request["kind"] == "dev"
        if detected_blender is not None:
            detected = normalize_detected_blender(detected_blender)

    resolvers = {
        SOURCE_MANUAL: _manual_source,
        SOURCE_PYTHON_API: _python_api_source,
        SOURCE_RELEASE_NOTES: _release_notes_source,
    }
    resolved_sources = [
        resolvers[source](
            version_request,
            effective_version,
            is_prerelease,
            language_context,
        )
        for source in normalized_sources
    ]

    for source in resolved_sources:
        parsed = urlparse(source["base_url"])
        if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_DOCUMENTATION_HOSTS:
            raise BlenderDocumentationContextError(
                "Internal source resolver produced a non-official Blender URL"
            )

    warnings: list[str] = []
    if not language_context["manual_language_supported"]:
        warnings.append(
            f"Manual language {language_context['requested']!r} is unsupported; using English"
        )
    if version_request["kind"] == "auto" and is_prerelease:
        warnings.append(
            "Connected Blender is a prerelease build; Manual and Python API resolve to dev"
        )
    if any(
        source["source"] == SOURCE_RELEASE_NOTES
        and source["channel"] == "index"
        for source in resolved_sources
    ):
        warnings.append(
            "Release Notes need a numeric Blender version; resolved to the official index"
        )

    return {
        "schema": DOCUMENTATION_CONTEXT_SCHEMA,
        "requested": {
            "version": version_request["requested"],
            "language": language_context["requested"],
            "sources": normalized_sources,
        },
        "detected_blender": detected,
        "resolved": {
            "version": effective_version,
            "release_cycle": release_cycle,
            "is_prerelease": is_prerelease,
            "language": language_context["manual_language"],
        },
        "sources": resolved_sources,
        "warnings": warnings,
    }
