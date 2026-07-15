"""Blender MCP host package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("blender-mcp")
except PackageNotFoundError:
    __version__ = "unknown"

from .host import get_blender_connection
from .transport.connection import BlenderConnection

__all__ = ["BlenderConnection", "__version__", "get_blender_connection"]
