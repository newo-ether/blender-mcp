"""Focused add-on runtime utilities for registration and occupancy UI."""

from __future__ import annotations

import os
import os.path as osp
import sys

import bpy

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except Exception:  # pragma: no cover - unavailable in background contexts
    gpu = None
    batch_for_shader = None


def runtime_directory():
    override = os.getenv("BLENDER_MCP_RUNTIME_DIR", "")
    if override:
        return osp.abspath(osp.expanduser(override))
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or osp.join(osp.expanduser("~"), "AppData", "Local")
        return osp.join(base, "BlenderMCP", "instances")
    if sys.platform == "darwin":
        return osp.join(osp.expanduser("~"), "Library", "Application Support", "BlenderMCP", "instances")
    runtime = os.getenv("XDG_RUNTIME_DIR", "")
    if runtime:
        return osp.join(runtime, "blender-mcp", "instances")
    state = os.getenv("XDG_STATE_HOME") or osp.join(osp.expanduser("~"), ".local", "state")
    return osp.join(state, "blender-mcp", "instances")


def tag_redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", []):
        for area in getattr(window.screen, "areas", []):
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def draw_occupancy_border():
    server = getattr(bpy.types, "blendermcp_server", None)
    if not server or not server.has_live_claim() or gpu is None or batch_for_shader is None:
        return
    region = getattr(bpy.context, "region", None)
    if not region or region.type != 'WINDOW':
        return
    inset = 8.0
    left, right = inset, max(inset, float(region.width) - inset)
    bottom, top = inset, max(inset, float(region.height) - inset)
    vertices = (
        (left, bottom), (right, bottom),
        (right, bottom), (right, top),
        (right, top), (left, top),
        (left, top), (left, bottom),
    )
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": vertices})
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(4.0)
        shader.bind()
        shader.uniform_float("color", (0.05, 0.85, 1.0, 0.95))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')


def on_allow_ai_control_changed(self, _context):
    server = getattr(bpy.types, "blendermcp_server", None)
    if server and not self.allow_ai_control:
        server.revoke_claim("claim_revoked_by_user")
    if server:
        server._write_registry_record()
    tag_redraw()
