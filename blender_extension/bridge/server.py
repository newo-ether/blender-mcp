from __future__ import annotations

from ..providers.generation import GenerationProvidersMixin
from ..providers.polyhaven import PolyhavenMixin
from .lifecycle import BridgeLifecycleMixin
from .nodes import NodeCommandsMixin
from .scene import SceneDiagnosticsMixin


class BlenderMCPServer(
    GenerationProvidersMixin,
    PolyhavenMixin,
    SceneDiagnosticsMixin,
    NodeCommandsMixin,
    BridgeLifecycleMixin,
):
    """Blender-side bridge composed from domain-specific capabilities."""
