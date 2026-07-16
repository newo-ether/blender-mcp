"""Portable Blender acceptance for the live Node Editor context state machine."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


class Pointer(SimpleNamespace):
    def as_pointer(self):
        return self.pointer


class Nodes(list):
    active = None


def named(name, **values):
    return SimpleNamespace(name=name, **values)


def make_editor(pointer, tree, owner, *, pinned=False):
    space = SimpleNamespace(
        id=owner,
        node_tree=tree,
        edit_tree=tree,
        path=[SimpleNamespace(node_tree=tree)],
        pin=pinned,
        tree_type=tree.bl_idname,
        shader_type="",
    )
    area = Pointer(
        pointer=pointer,
        type="NODE_EDITOR",
        ui_type=tree.bl_idname,
        x=0,
        y=0,
        width=800,
        height=600,
        spaces=SimpleNamespace(active=space),
    )
    return area


def main():
    namespace = runpy.run_path(
        str(REPO_ROOT / "tests" / "blender_extension_namespace.py"),
        run_name="blender_mcp_node_editor_context_acceptance",
    )
    server = namespace["BlenderMCPServer"]()
    response = server._execute_command_internal({
        "type": "get_node_editor_context",
        "params": {},
    })
    headless = response["result"]
    assert headless.get("state") == "NO_EDITOR", headless
    assert headless["observed_state"] == "NO_EDITOR"
    assert headless["selected_context_id"] is None

    from blender_extension.nodes.editor_context import _node_editor_context_from_windows

    nodes = Nodes([
        named("Input", select=True),
        named("Output", select=True),
        named("Unselected", select=False),
    ])
    nodes.active = nodes[1]
    tree = named(
        "Graph",
        bl_idname="GeometryNodeTree",
        library=None,
        nodes=nodes,
    )
    owner = named(
        "Graph",
        bl_rna=SimpleNamespace(identifier="GeometryNodeTree"),
        library=None,
    )
    tree_ref = {
        "tree_type": "GeometryNodeTree",
        "owner": {"kind": "NODE_GROUP", "name": "Graph"},
    }
    targets = [{"tree": tree, "owner_id": owner, "tree_ref": tree_ref}]
    area = make_editor(0x20, tree, owner)
    window = Pointer(
        pointer=0x10,
        screen=named("Layout", areas=[area]),
        workspace=named("Geometry Nodes"),
    )

    unique = _node_editor_context_from_windows(
        [window], "file-one", targets=targets
    )
    assert unique["state"] == "UNIQUE_EDITOR"
    assert unique["selected_context_id"] == "window:10/area:20"
    assert unique["editors"][0]["tree_ref"] == tree_ref
    assert unique["editors"][0]["active_node"] == "Output"
    assert unique["editors"][0]["selected_nodes"] == ["Input", "Output"]
    assert unique == _node_editor_context_from_windows(
        [window], "file-one", targets=targets
    )

    area.spaces.active.pin = True
    pinned = _node_editor_context_from_windows(
        [window], "file-one", targets=targets
    )
    assert pinned["state"] == "PINNED_EDITOR"
    area.spaces.active.pin = False

    second = make_editor(0x30, tree, owner)
    window.screen.areas.append(second)
    multiple = _node_editor_context_from_windows(
        [window], "file-one", targets=targets, max_editors=1
    )
    assert multiple["state"] == "MULTIPLE_EDITORS"
    assert multiple["selected_context_id"] is None
    assert multiple["total_editors"] == 2
    assert multiple["truncated"] is True
    assert len(multiple["editors"]) == 1

    stale = _node_editor_context_from_windows(
        [window],
        "file-two",
        targets=targets,
        expected_file_session_id="file-one",
        expected_context_revision=unique["context_revision"],
    )
    assert stale["state"] == "STALE_CONTEXT"
    assert stale["selected_context_id"] is None
    assert stale["stale_reasons"] == ["file_session_changed", "context_changed"]
    json.dumps(stale, ensure_ascii=False)
    print("BLENDER_MCP_NODE_EDITOR_CONTEXT=ok")


if __name__ == "__main__":
    main()
