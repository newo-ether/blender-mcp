"""Seed and verify Blender preferences around an installer acceptance run."""

from __future__ import annotations

import json
import sys

import bpy


MODULE_ID = "bl_ext.user_default.blender_mcp"


def _mode() -> str:
    try:
        separator = sys.argv.index("--")
        return sys.argv[separator + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError("Expected `-- seed` or `-- verify`") from exc


def _state() -> dict[str, object]:
    preferences = bpy.context.preferences
    addon = preferences.addons.get(MODULE_ID)
    return {
        "show_splash": preferences.view.show_splash,
        "use_emulate_numpad": preferences.inputs.use_emulate_numpad,
        "save_version": preferences.filepaths.save_version,
        "addon_enabled": addon is not None,
        "auto_connect": addon.preferences.auto_connect if addon else None,
    }


mode = _mode()
preferences = bpy.context.preferences
if mode == "seed":
    preferences.view.show_splash = False
    preferences.inputs.use_emulate_numpad = True
    preferences.filepaths.save_version = 7
    bpy.ops.wm.save_userpref()
elif mode == "verify":
    state = _state()
    assert state == {
        "show_splash": False,
        "use_emulate_numpad": True,
        "save_version": 7,
        "addon_enabled": True,
        "auto_connect": True,
    }, state
else:
    raise RuntimeError(f"Unknown mode: {mode}")

print("BLENDER_MCP_INSTALLER_PREFERENCES=" + json.dumps(_state(), sort_keys=True))
