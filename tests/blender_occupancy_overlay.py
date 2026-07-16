"""Headless Blender test: the occupancy overlay must agree with the claim lock.

The border means "the AI has changed something here, so your own edits may
collide". That is true of exactly the commands the claim guard demands a claim
for. If the two ever disagree, the border either warns about a harmless read or
stays dark through a real edit, and a border that lies is worse than no border.

The split is verified against every dispatchable command rather than a sample,
because the failure mode is a newly added command quietly landing on the wrong
side of it.
"""

from __future__ import annotations

import json
import re
import runpy
import sys
import time
import traceback
from pathlib import Path

import bpy

RESULT_PREFIX = "BLENDER_MCP_OVERLAY_RESULT="
REPO_ROOT = Path(__file__).resolve().parents[1]

# Dict keys in lifecycle.py that the handler regex also matches.
_NON_COMMAND_KEYS = {
    "claim", "host", "expires_at", "client_id", "owner_label",
    "port", "token", "lease_seconds",
}


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def dispatchable_commands():
    source = (REPO_ROOT / "blender_extension" / "bridge" / "lifecycle.py").read_text(
        encoding="utf-8"
    )
    names = set(re.findall(r'"([a-z0-9_3]+)"\s*:\s*self\.[A-Za-z0-9_]+', source))
    return sorted(names - _NON_COMMAND_KEYS)


class FakeSpace:
    def __init__(self, tree_type):
        self.tree_type = tree_type


class FakeSpaces:
    def __init__(self, tree_type):
        self.active = FakeSpace(tree_type)


class FakeArea:
    """Stands in for an editor so every node system can be checked cheaply."""

    def __init__(self, area_type, tree_type=None):
        self.type = area_type
        self.spaces = FakeSpaces(tree_type)


def make_server(namespace, claimed=True):
    server = namespace["BlenderMCPServer"]()
    server.claim = (
        {
            "client_id": "client",
            "token": "token",
            "owner_label": "Claude Code",
            "expires_at": time.time() + 120.0,
            "lease_seconds": 120.0,
        }
        if claimed
        else None
    )
    server.active_command = ""
    server.active_tree_type = ""
    return server


def run_command(namespace, server, command, params=None):
    """Mirror the dispatch's overlay bookkeeping for one command."""
    params = params or {}
    if server._command_writes(command, params):
        server.active_command = command
        tree_type = namespace["_command_tree_type"](command, params)
        if tree_type:
            server.active_tree_type = tree_type


def run_test():
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_overlay_test",
    )
    from blender_extension import runtime
    from blender_extension.bridge import lifecycle

    namespace = dict(namespace)
    namespace["_command_tree_type"] = lifecycle._command_tree_type

    errors = namespace["BlenderMCPAddonError"]
    commands = dispatchable_commands()
    assert_true(len(commands) > 40, f"handler discovery found only {len(commands)} commands")

    # 1. The border's notion of "writes" must equal the lock's notion of
    #    "needs a claim", for every command the bridge can dispatch.
    unclaimed = make_server(namespace, claimed=False)
    claimed = make_server(namespace)
    for command in commands:
        try:
            unclaimed._authorize_command(command, {})
            needs_claim = False
        except errors:
            needs_claim = True
        assert_true(
            needs_claim == claimed._command_writes(command, {}),
            f"{command}: border and claim lock disagree about whether it writes",
        )

    # 2. The conditional write follows the guard's own exception.
    assert_true(
        not claimed._command_writes("ensure_scene_compositor_tree", {"create_if_missing": False}),
        "a read-only compositor probe must not light the border",
    )
    assert_true(
        claimed._command_writes("ensure_scene_compositor_tree", {"create_if_missing": True}),
        "creating a compositor tree is a write and must light the border",
    )

    # 3. Reads leave the overlay completely dark.
    reader = make_server(namespace)
    for command in sorted(lifecycle.READ_ONLY_COMMANDS):
        run_command(namespace, reader, command, {"tree_ref": {"tree_type": "GeometryNodeTree"}})
    assert_true(reader.active_command == "", "a read left the badge naming a command")
    assert_true(reader.active_tree_type == "", "a read lit a node editor")

    viewport = FakeArea("VIEW_3D")
    assert_true(
        not runtime._area_is_occupied(viewport, bool(reader.active_command), ""),
        "the viewport was bordered for a claim that has only read",
    )

    # 4. A write lights the viewport and names itself, node work or not.
    writer = make_server(namespace)
    run_command(namespace, writer, "bake_simulation", {})
    assert_true(writer.active_command == "bake_simulation", "write did not record its command")
    assert_true(writer.active_tree_type == "", "a non-node write lit a node editor")
    assert_true(
        runtime._area_is_occupied(viewport, True, ""),
        "the viewport must border after any write",
    )
    assert_true(
        runtime._occupancy_status(writer) == "Claude Code · bake_simulation",
        f"badge text is wrong: {runtime._occupancy_status(writer)!r}",
    )

    # 5. Each node system lights only its own editor.
    systems = ("GeometryNodeTree", "ShaderNodeTree", "CompositorNodeTree")
    for shown in systems:
        editor = FakeArea("NODE_EDITOR", shown)
        for active in systems:
            assert_true(
                runtime._area_is_occupied(editor, True, active) == (shown == active),
                f"a {shown} editor lit for {active} work",
            )
        assert_true(
            not runtime._area_is_occupied(editor, False, shown),
            "a node editor bordered before any write",
        )

    geometry = make_server(namespace)
    run_command(namespace, geometry, "apply_geometry_node_patch", {"tree_name": "Spring Motion"})
    assert_true(
        geometry.active_tree_type == "GeometryNodeTree",
        "geometry commands carry no tree_type and must be identified by name",
    )
    shader = make_server(namespace)
    run_command(namespace, shader, "apply_node_tree_patch", {"tree_ref": {"tree_type": "ShaderNodeTree"}})
    assert_true(shader.active_tree_type == "ShaderNodeTree", "tree_ref tree_type was not honoured")

    # 6. Losing the claim clears the overlay, so it can never describe a
    #    session that is over. This is what left stale borders behind before.
    expired = make_server(namespace)
    run_command(namespace, expired, "apply_geometry_node_patch", {"tree_name": "Spring Motion"})
    expired.claim["expires_at"] = time.time() - 1.0
    assert_true(not expired.has_live_claim(), "an expired claim reported itself live")
    assert_true(expired.claim is None, "expiry did not drop the claim")
    assert_true(expired.active_command == "", "expiry left the badge naming a command")
    assert_true(expired.active_tree_type == "", "expiry left a node editor lit")
    assert_true(runtime._occupancy_status(expired) is None, "expiry left badge text behind")

    # 7. The overlay must reach every editor it draws in, or a border can
    #    survive the claim that justified it.
    assert_true(
        "SpaceNodeEditor" in runtime.OVERLAY_SPACE_TYPES
        and "SpaceView3D" in runtime.OVERLAY_SPACE_TYPES,
        f"overlay does not cover both editors: {runtime.OVERLAY_SPACE_TYPES}",
    )

    return {
        "blender_version": list(bpy.app.version[:3]),
        "commands_checked": len(commands),
        "read_only_commands": len(lifecycle.READ_ONLY_COMMANDS),
        "overlay_space_types": list(runtime.OVERLAY_SPACE_TYPES),
    }


try:
    result = run_test()
    print(RESULT_PREFIX + json.dumps({"ok": True, **result}, sort_keys=True))
except Exception as exc:
    traceback.print_exc()
    print(
        RESULT_PREFIX
        + json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True)
    )
    sys.exit(1)
