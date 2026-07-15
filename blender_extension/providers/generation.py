"""Composition facade for optional model-generation providers."""

from .hunyuan import HunyuanProviderMixin
from .hyper3d import Hyper3DProviderMixin
from .sketchfab import SketchfabProviderMixin
from .status import GenerationStatusMixin


class GenerationProvidersMixin(
    HunyuanProviderMixin,
    SketchfabProviderMixin,
    Hyper3DProviderMixin,
    GenerationStatusMixin,
):
    """Combine optional provider capabilities for the Blender bridge."""
