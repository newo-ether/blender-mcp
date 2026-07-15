from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from mcp.server.fastmcp import Context

from ..documentation.client import BlenderDocumentationClient
from ..documentation.context import (
    BlenderDocumentationContextError,
    resolve_documentation_context,
    version_requires_blender,
)
from ..documentation.http import BlenderDocumentationRetrievalError
from ..host import get_blender_connection, mcp
from ..observability.decorators import telemetry_tool

logger = logging.getLogger("BlenderMCPServer")

@mcp.tool()
@telemetry_tool("get_blender_documentation_context")
def get_blender_documentation_context(
    ctx: Context,
    version: str = "auto",
    language: str = "en",
    sources: List[str] = None,
    user_prompt: str = "",
) -> str:
    """Resolve version-correct official Blender documentation sources.

    This tool performs no documentation network request. With version="auto"
    it reads exact build metadata from the connected Blender instance. Explicit
    major.minor, current, and dev requests work without a Blender connection.

    Parameters:
    - version: auto, current, dev, or major.minor[.patch]
    - language: Blender Manual language code, for example en or zh-hans
    - sources: manual, python_api, and/or release_notes
    - user_prompt: Original user prompt for telemetry
    """
    try:
        result = _resolve_blender_documentation_context(
            version=version,
            language=language,
            sources=sources,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except BlenderDocumentationContextError as e:
        logger.error(f"Invalid Blender documentation context request: {str(e)}")
        return f"Error resolving Blender documentation context: {str(e)}"
    except Exception as e:
        logger.error(f"Error resolving Blender documentation context: {str(e)}")
        return f"Error resolving Blender documentation context: {str(e)}"

def _resolve_blender_documentation_context(
    *,
    version: str,
    language: str,
    sources: List[str],
) -> Dict[str, Any]:
    """Resolve source context, consulting Blender only for version=auto."""
    detected = None
    if version_requires_blender(version):
        blender = get_blender_connection()
        detected = blender.send_command("get_blender_version_context")
    return resolve_documentation_context(
        version=version,
        language=language,
        sources=sources,
        detected_blender=detected,
    )

@mcp.tool()
@telemetry_tool("search_blender_docs")
def search_blender_docs(
    ctx: Context,
    query: str,
    version: str = "auto",
    sources: List[str] = None,
    language: str = "en",
    limit: int = 8,
    snippet_mode: str = "top",
    user_prompt: str = "",
) -> str:
    """Search version-correct official Blender documentation.

    Search is bounded to official Blender Manual, Python API, and Release Notes
    indexes. Results include source/version/fallback metadata and canonical URLs.

    Parameters:
    - query: Search text, up to 200 characters
    - version: auto, current, dev, or major.minor[.patch]
    - sources: manual, python_api, and/or release_notes; defaults to manual
    - language: Blender Manual language code, for example en or zh-hans
    - limit: Maximum results from 1 to 20
    - snippet_mode: none, top (default, first three), or all
    - user_prompt: Original user prompt for telemetry
    """
    try:
        context = _resolve_blender_documentation_context(
            version=version,
            language=language,
            sources=sources or ["manual"],
        )
        result = BlenderDocumentationClient().search(
            context,
            query=query,
            limit=limit,
            snippet_mode=snippet_mode,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (BlenderDocumentationContextError, BlenderDocumentationRetrievalError) as e:
        logger.error(f"Invalid Blender documentation search: {str(e)}")
        return f"Error searching Blender documentation: {str(e)}"
    except Exception as e:
        logger.error(f"Error searching Blender documentation: {str(e)}")
        return f"Error searching Blender documentation: {str(e)}"

@mcp.tool()
@telemetry_tool("get_blender_doc_page")
def get_blender_doc_page(
    ctx: Context,
    page: str,
    version: str = "auto",
    source: str = "manual",
    language: str = "en",
    heading: str = "",
    max_chars: int = 12000,
    user_prompt: str = "",
) -> str:
    """Fetch one bounded section from official Blender documentation.

    The page parameter is a source-relative identifier, never an arbitrary URL.
    Scripts, styles, navigation, and other page chrome are removed.

    Parameters:
    - page: Relative Manual/API/Release Notes page identifier
    - version: auto, current, dev, or major.minor[.patch]
    - source: manual, python_api, or release_notes
    - language: Blender Manual language code, for example en or zh-hans
    - heading: Optional exact heading whose section should be returned
    - max_chars: Output bound from 100 to 50000 characters
    - user_prompt: Original user prompt for telemetry
    """
    try:
        context = _resolve_blender_documentation_context(
            version=version,
            language=language,
            sources=[source],
        )
        canonical_source = context["sources"][0]["source"]
        result = BlenderDocumentationClient().get_page(
            context,
            page=page,
            source=canonical_source,
            heading=heading or None,
            max_chars=max_chars,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except (BlenderDocumentationContextError, BlenderDocumentationRetrievalError) as e:
        logger.error(f"Invalid Blender documentation page request: {str(e)}")
        return f"Error getting Blender documentation page: {str(e)}"
    except Exception as e:
        logger.error(f"Error getting Blender documentation page: {str(e)}")
        return f"Error getting Blender documentation page: {str(e)}"
