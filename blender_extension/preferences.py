"""Preference lookup shared by UI and bridge lifecycle code."""

from __future__ import annotations

import bpy

from . import state


def get_blendermcp_addon_preferences(context=None):
    """Return the active add-on preferences object when registered."""
    if context is None:
        context = bpy.context
    addon = context.preferences.addons.get(state.addon_module_id)
    return addon.preferences if addon else None
