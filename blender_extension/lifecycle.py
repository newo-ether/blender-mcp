from __future__ import annotations

import uuid

import bpy

from . import state

_PREF_PROPERTY_NAMES = [
    'allow_ai_control', 'use_polyhaven',
    'use_hyper3d', 'hyper3d_mode', 'hyper3d_api_key',
    'use_sketchfab', 'sketchfab_api_key',
    'use_hunyuan3d', 'hunyuan3d_mode',
    'hunyuan3d_secret_id', 'hunyuan3d_secret_key',
    'hunyuan3d_api_url', 'hunyuan3d_octree_resolution',
    'hunyuan3d_num_inference_steps', 'hunyuan3d_guidance_scale',
    'hunyuan3d_texture',
]

def _make_scene_update(prop_name):
    """Factory: returns an update callback that syncs the scene value to AddonPreferences."""
    def update(self, context):
        addon_prefs = context.preferences.addons.get(state.addon_module_id)
        if addon_prefs and hasattr(addon_prefs.preferences, prop_name):
            setattr(addon_prefs.preferences, prop_name, getattr(self, f'blendermcp_{prop_name}'))
    return update

def sync_prefs_to_scene():
    """Copy all persistent AddonPreferences values to the current scene."""
    addon_prefs = bpy.context.preferences.addons.get(state.addon_module_id)
    if not addon_prefs:
        return
    prefs = addon_prefs.preferences
    scene = bpy.context.scene
    for name in _PREF_PROPERTY_NAMES:
        pref_val = getattr(prefs, name, None)
        scene_attr = f'blendermcp_{name}'
        if hasattr(scene, scene_attr):
            try:
                setattr(scene, scene_attr, pref_val)
            except (AttributeError, TypeError):
                pass

def _auto_connect_if_enabled():
    from .bridge.server import BlenderMCPServer
    """Start MCP server if auto_connect is enabled and not already running."""
    addon_prefs = bpy.context.preferences.addons.get(state.addon_module_id)
    if not addon_prefs:
        return
    if not addon_prefs.preferences.auto_connect:
        return
    existing_server = getattr(bpy.types, "blendermcp_server", None)
    if existing_server and existing_server.running:
        bpy.context.scene.blendermcp_server_running = True
        return

    server = BlenderMCPServer()
    bpy.types.blendermcp_server = server
    server.start()
    bpy.context.scene.blendermcp_server_running = server.running

@bpy.app.handlers.persistent
def _load_post_handler(_dummy):
    """On .blend file load: sync prefs → scene, auto-connect if enabled."""
    state.file_session_id = str(uuid.uuid4())
    server = getattr(bpy.types, "blendermcp_server", None)
    if server:
        server.rotate_file_session()
    sync_prefs_to_scene()
    _auto_connect_if_enabled()
