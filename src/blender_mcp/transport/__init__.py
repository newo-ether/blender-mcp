"""Blender bridge transport and instance routing."""

from .connection import BlenderConnection
from .instances import InstanceConnectionManager, discover_registry_records

__all__ = [
    "BlenderConnection",
    "InstanceConnectionManager",
    "discover_registry_records",
]
