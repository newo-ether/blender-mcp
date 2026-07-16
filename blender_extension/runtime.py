"""Focused add-on runtime utilities for registration and occupancy UI."""

from __future__ import annotations

import os
import os.path as osp
import sys
from contextlib import suppress

import bpy

try:
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
except Exception:  # pragma: no cover - unavailable in background contexts
    blf = None
    gpu = None
    batch_for_shader = None

# Editors the occupancy overlay can draw in. Blender has no window-level drawing
# layer: a draw handler is bound to one SpaceType and its callback is clipped to
# that region's own coordinates, so each editor that shows the border needs its
# own handler.
#
# Only these two. Bordering every editor was tried and reads as noise: a frame
# around all six areas of a normal layout stops conveying anything. The 3D
# viewport stands in for the application as a whole, and the node editor is
# where most AI node work is actually visible.
OVERLAY_SPACE_TYPES = (
    "SpaceView3D",
    "SpaceNodeEditor",
)

_OVERLAY_COLOR = (0.05, 0.85, 1.0, 0.95)
_OVERLAY_INSET = 8.0


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
    """Refresh every editor the overlay can draw in, not just the 3D viewport.

    The overlay is not viewport-only: AI work happens just as often in a Node
    Editor or the Spreadsheet, so those areas have to repaint when the claim or
    the running command changes.
    """
    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", []):
        screen = getattr(window, "screen", None)
        for area in getattr(screen, "areas", []):
            area.tag_redraw()


def _area_is_occupied(area, has_written, tree_type):
    """Return True when this editor should show the occupancy border.

    The border means "the AI has changed something here, so your own edits may
    collide". It is therefore driven by whether a write has happened, not by
    the claim alone: a client that only reads cannot collide with the user, so
    bordering the viewport for it would warn about nothing.

    - The 3D viewport carries the border once the AI has written anything at
      all. It stands in for the application as a whole, so it answers "has this
      Blender been changed" no matter which editor the user is looking at.
    - A node editor joins it once the AI has written to that kind of node tree,
      and both stay lit until the claim ends. They mark "the AI has been
      working here", not "a command is running this instant", so they do not
      flicker on and off around each command.

    Matching on the editor's tree_type -- not on a specific tree name -- keeps
    this predictable: it does not depend on whether one particular tree happens
    to be open, and the values are the same strings the commands already use
    (GeometryNodeTree, ShaderNodeTree, CompositorNodeTree).
    """
    if not has_written:
        return False
    if area.type == 'VIEW_3D':
        return True
    if area.type == 'NODE_EDITOR' and tree_type:
        return getattr(area.spaces.active, "tree_type", None) == tree_type
    return False


def _occupancy_status(server):
    """Return the badge text naming the AI's most recent command, or None.

    Reports the last command rather than only a currently-running one: most
    commands finish in milliseconds, so a badge tied to execution would flicker
    past unread. Naming the last one answers "what has the AI just done", which
    stays legible and stays true. It is cleared when the claim ends, so it can
    never describe a session that is over.
    """
    command = str(getattr(server, "active_command", "") or "")
    if not command:
        return None
    claim = getattr(server, "claim", None)
    owner = ""
    if isinstance(claim, dict):
        owner = str(claim.get("owner_label") or "")
    return f"{owner or 'MCP client'} · {command}"


def _draw_occupancy_badge(region, text):
    """Draw one small label in the region's bottom-left corner.

    Deliberately a corner label rather than a full-width banner: the border
    already carries "occupied", so this only has to answer "by whom, doing
    what" without covering the work underneath.

    Bottom-left, not top-left: Blender draws its own view name and collection
    text in the top-left of the 3D viewport, and the badge would sit on top of
    it. Sizes follow the user's interface scale, since a fixed pixel size is
    unreadable on a scaled display.
    """
    if blf is None:
        return
    scale = 1.0
    with suppress(Exception):
        scale = float(bpy.context.preferences.system.ui_scale) or 1.0

    font = 0
    blf.size(font, int(round(12 * scale)))
    text_width, text_height = blf.dimensions(font, text)

    padding = 6.0 * scale
    left = _OVERLAY_INSET + 4.0 * scale
    bottom = _OVERLAY_INSET + 4.0 * scale
    box_left = left
    box_right = left + text_width + padding * 2.0
    box_bottom = bottom
    box_top = bottom + text_height + padding * 2.0
    if box_right > float(region.width) or box_top > float(region.height):
        # Region too small to place the badge without overlapping the border.
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(
        shader,
        'TRIS',
        {
            "pos": (
                (box_left, box_bottom), (box_right, box_bottom), (box_right, box_top),
                (box_left, box_bottom), (box_right, box_top), (box_left, box_top),
            )
        },
    )
    shader.bind()
    shader.uniform_float("color", (0.0, 0.0, 0.0, 0.55))
    batch.draw(shader)

    blf.color(font, *_OVERLAY_COLOR)
    blf.position(font, box_left + padding, box_bottom + padding, 0.0)
    blf.draw(font, text)


def draw_occupancy_border():
    server = getattr(bpy.types, "blendermcp_server", None)
    if not server or not server.has_live_claim() or gpu is None or batch_for_shader is None:
        return
    region = getattr(bpy.context, "region", None)
    if not region or region.type != 'WINDOW':
        return
    current_area = getattr(bpy.context, "area", None)
    # active_command is only ever set by a command that can change Blender, so
    # its presence is exactly "this claim has written something".
    has_written = bool(getattr(server, "active_command", ""))
    if current_area is None or not _area_is_occupied(
        current_area, has_written, str(getattr(server, "active_tree_type", "") or "")
    ):
        return
    inset = _OVERLAY_INSET
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
        shader.uniform_float("color", _OVERLAY_COLOR)
        batch.draw(shader)
        status = _occupancy_status(server)
        if status:
            _draw_occupancy_badge(region, status)
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
