from __future__ import annotations

import copy
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = runpy.run_path(str(ROOT / "blender_extension/nodes/layout_core.py"))
audit_layout, plan_layout = CORE["audit_layout"], CORE["plan_layout"]


def sample(domain="GeometryNodeTree"):
    nodes = {}
    for name, kind, parent, rect in [
        ("Frame", "NodeFrame", None, [0, 0, 800, -600]),
        ("Input", "NodeGroupInput", "Frame", [30, -40, 170, -100]),
        ("Math", "ShaderNodeMath", "Frame", [230, -40, 370, -220]),
        ("Math.001", "ShaderNodeMath", "Frame", [430, -40, 570, -220]),
        ("Output", "NodeGroupOutput", "Frame", [630, -40, 770, -100]),
    ]:
        nodes[name] = {"name": name, "label": "Stage" if kind == "NodeFrame" else "", "bl_idname": kind,
                       "layout": {"parent": parent, "bounds": rect, "bounds_status": "refreshed", "absolute_location": rect[:2]}}
    links = [{"from_node": a, "from_socket": "output:0:Value", "to_node": b, "to_socket": "input:0:Value"}
             for a, b in zip(["Input", "Math", "Math.001"], ["Math", "Math.001", "Output"])]
    return {"schema": "blender-node-layout/1", "scope": {"kind": "full"}, "revision": "sha256:" + "a" * 64,
            "tree_ref": {"tree_type": domain, "owner": {"kind": "NODE_GROUP", "name": "Fixture"}},
            "capabilities": {"editable": True}, "tree": {"name": "Fixture", "nodes": nodes, "links": links},
            "stats": {"node_count": 5, "total_node_count": 5}}


class LayoutCoreTests(unittest.TestCase):
    def test_parent_containment_is_not_an_overlap(self):
        result = audit_layout(sample())
        self.assertTrue(result["valid"])
        self.assertTrue(result["bounds_verified"])
        self.assertNotIn("node_overlap", result["diagnostic_counts"])

    def test_empty_tree_does_not_generate_an_invalid_empty_patch(self):
        source = sample()
        source["tree"].update(nodes={}, links=[])
        source["stats"].update(node_count=0, total_node_count=0)
        result = plan_layout(source)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["patch"])

    def test_overlap_and_parent_escape_are_diagnosed(self):
        source = sample()
        source["tree"]["nodes"]["Math"]["layout"]["bounds"] = [20, 10, 220, -210]
        result = audit_layout(source)
        self.assertFalse(result["valid"])
        self.assertIn("outside_parent_frame", result["diagnostic_counts"])
        self.assertIn("node_overlap", result["diagnostic_counts"])

    def test_cached_bounds_do_not_claim_verified_layout(self):
        source = sample()
        source["tree"]["nodes"]["Input"]["layout"]["bounds_status"] = "cached"
        result = audit_layout(source)
        self.assertFalse(result["bounds_verified"])
        self.assertIn("dimensions_unverified", result["diagnostic_counts"])
        self.assertFalse(result["visual_layout_checked"])

    def test_plans_are_deterministic_non_mutating_and_domain_valid(self):
        from blender_mcp.protocol.geometry_nodes import assert_valid_patch as geometry_validate
        from blender_mcp.protocol.node_patch import assert_valid_patch as generic_validate
        for domain in ("GeometryNodeTree", "ShaderNodeTree", "CompositorNodeTree"):
            source = sample(domain)
            original = copy.deepcopy(source)
            result = plan_layout(source, main_path=["Math", "Math.001", "Output"])
            self.assertEqual(result, plan_layout(source, main_path=["Math", "Math.001", "Output"]))
            self.assertEqual(source, original)
            self.assertEqual(result["status"], "planned")
            self.assertTrue(result["predicted_audit"]["valid"])
            self.assertFalse(result["predicted_audit"]["bounds_verified"])
            (geometry_validate if domain == "GeometryNodeTree" else generic_validate)(result["patch"])
            self.assertTrue(all(op["op"] == "set_node_layout" for op in result["patch"]["operations"]))

    def test_tiny_frames_and_unframed_outputs_block_instead_of_being_restructured(self):
        source = sample()
        source["tree"]["nodes"]["Output"]["layout"]["parent"] = None
        result = plan_layout(source)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["patch"])

    def test_missing_node_measurements_block_but_frame_sizes_are_derived(self):
        source = sample()
        source["tree"]["nodes"]["Frame"]["layout"]["bounds"] = None
        self.assertEqual(plan_layout(source)["status"], "planned")
        source["tree"]["nodes"]["Math"]["layout"]["bounds"] = None
        self.assertEqual(plan_layout(source)["status"], "blocked")

    def test_cycles_and_invalid_spacing_do_not_produce_a_patch(self):
        source = sample()
        source["tree"]["links"].append({"from_node": "Output", "to_node": "Input", "from_socket": "output:0:V", "to_socket": "input:0:V"})
        self.assertEqual(plan_layout(source)["status"], "blocked")
        for value in (float("nan"), -1, float("inf")):
            with self.assertRaises(ValueError):
                plan_layout(sample(), horizontal_gap=value)

    def test_diagnostic_truncation_keeps_complete_counts(self):
        source = sample()
        for node in source["tree"]["nodes"].values():
            node["layout"]["bounds"] = None
        result = audit_layout(source, diagnostic_limit=1)
        self.assertTrue(result["diagnostics_truncated"])
        self.assertEqual(result["diagnostic_counts"]["dimensions_unavailable"], 5)
        source = sample()
        source["tree"]["nodes"]["Frame"]["label"] = ""
        source["tree"]["nodes"]["Math"]["layout"]["bounds"] = [30, -40, 170, -220]
        result = audit_layout(source, diagnostic_limit=1)
        self.assertEqual(result["diagnostics"][0]["severity"], "warning")
        self.assertFalse(result["valid"], "Truncating diagnostics must not hide later errors")
        self.assertIn("node_overlap", result["diagnostic_counts"])

    def test_nested_frames_are_single_units_and_preserve_local_coordinates(self):
        source = sample()
        outer = source["tree"]["nodes"]["Frame"]
        nodes = {"Outer": copy.deepcopy(outer)}
        links = []
        for index in range(4):
            child = sample()
            for name, node in child["tree"]["nodes"].items():
                key = f"{index}.{name}"
                node["name"] = key
                node["layout"]["parent"] = "Outer" if name == "Frame" else f"{index}.Frame"
                nodes[key] = node
            for link in child["tree"]["links"]:
                link["from_node"] = f"{index}." + link["from_node"]
                link["to_node"] = f"{index}." + link["to_node"]
                links.append(link)
            if index:
                links.append({"from_node": f"{index - 1}.Output", "to_node": f"{index}.Input",
                              "from_socket": "output:0:V", "to_socket": "input:0:V"})
        source["tree"].update(nodes=nodes, links=links)
        source["stats"].update(node_count=len(nodes), total_node_count=len(nodes))
        result = plan_layout(source)
        self.assertEqual(result["status"], "planned")
        self.assertTrue(result["predicted_audit"]["valid"])
        self.assertEqual(set(result["predicted_audit"]["frame_child_counts"].values()), {4})
        positions = {op["node"]: op["location"] for op in result["patch"]["operations"]}
        self.assertEqual(positions["0.Input"], positions["3.Input"])
        self.assertLess(positions["0.Frame"][0], positions["3.Frame"][0])
        self.assertTrue(all("parent" not in op for op in result["patch"]["operations"]))

    def test_frame_maximum_and_parent_cycles_block_planning(self):
        source = sample()
        for index in range(16):
            source["tree"]["nodes"][f"Extra{index}"] = copy.deepcopy(source["tree"]["nodes"]["Math"])
        source["stats"]["total_node_count"] = len(source["tree"]["nodes"])
        self.assertEqual(audit_layout(source)["frame_child_counts"]["Frame"], 20)
        self.assertEqual(plan_layout(source)["status"], "blocked")
        source = sample()
        source["tree"]["nodes"]["Frame"]["layout"]["parent"] = "Frame"
        self.assertIn("frame_parent_cycle", audit_layout(source)["diagnostic_counts"])
        self.assertEqual(plan_layout(source)["status"], "blocked")

    def test_socket_order_and_main_path_prioritize_feeders(self):
        source = sample()
        source["tree"]["links"] = [
            {"from_node": "Input", "to_node": "Math.001", "from_socket": "output:0:V", "to_socket": "input:1:V"},
            {"from_node": "Math", "to_node": "Math.001", "from_socket": "output:0:V", "to_socket": "input:0:V"},
            {"from_node": "Math.001", "to_node": "Output", "from_socket": "output:0:V", "to_socket": "input:0:V"},
        ]
        positions = lambda result: {op["node"]: op["location"] for op in result["patch"]["operations"]}
        ordered = positions(plan_layout(source))
        self.assertGreater(ordered["Math"][1], ordered["Input"][1])
        prioritized = positions(plan_layout(source, main_path=["Input", "Math.001", "Output"]))
        self.assertGreater(prioritized["Input"][1], prioritized["Math"][1])

    def test_partial_snapshots_and_oversized_plans_are_rejected(self):
        source = sample()
        source["scope"]["kind"] = "filtered"
        with self.assertRaises(ValueError):
            plan_layout(source)
        source = sample()
        for index in range(500):
            source["tree"]["nodes"][f"Extra{index}"] = copy.deepcopy(source["tree"]["nodes"]["Math"])
        source["stats"]["total_node_count"] = len(source["tree"]["nodes"])
        with self.assertRaisesRegex(ValueError, "500"):
            plan_layout(source)

    def test_auxiliary_inputs_leave_incoming_main_path_corridor_open(self):
        source = sample()
        source["tree"]["links"] = [
            {"from_node": "Input", "to_node": "Math", "from_socket": "output:0:V", "to_socket": "input:1:V"},
            {"from_node": "Math", "to_node": "Math.001", "from_socket": "output:0:V", "to_socket": "input:0:V"},
            {"from_node": "Math.001", "to_node": "Output", "from_socket": "output:0:V", "to_socket": "input:0:V"},
        ]
        result = plan_layout(source, main_path=["Math", "Math.001", "Output"])
        positions = {op["node"]: op["location"] for op in result["patch"]["operations"]}
        self.assertLess(positions["Input"][1], positions["Math"][1] - 180)

    def test_late_parameter_is_placed_next_to_its_consumer(self):
        source = sample()
        node = copy.deepcopy(source["tree"]["nodes"]["Input"])
        source["tree"]["nodes"]["Parameter"] = node
        source["stats"]["total_node_count"] = 6
        source["tree"]["links"].append({"from_node": "Parameter", "to_node": "Output", "from_socket": "output:0:V", "to_socket": "input:1:V"})
        result = plan_layout(source, main_path=["Input", "Math", "Math.001", "Output"])
        positions = {op["node"]: op["location"] for op in result["patch"]["operations"]}
        self.assertEqual(positions["Parameter"][0], positions["Math.001"][0])

    def test_plan_and_audit_schemas_match_all_statuses(self):
        import jsonschema
        audit_schema = json.loads((ROOT / "schemas/node-layout-audit-v1.json").read_text())
        plan_schema = json.loads((ROOT / "schemas/node-layout-plan-v1.json").read_text())
        jsonschema.validate(audit_layout(sample()), audit_schema)
        jsonschema.validate(plan_layout(sample()), plan_schema)
        source = sample()
        source["tree"]["nodes"]["Input"]["layout"]["parent"] = None
        jsonschema.validate(plan_layout(source), plan_schema)

    def test_headless_measurement_schema_accepts_unknown_ui_scale(self):
        import jsonschema
        source = sample()
        source["measurement"] = {"units": "canvas", "ui_scale": 0, "refreshed": False,
                                 "source_revision_excludes_measurements": True, "socket_positions_available": False}
        source["stats"]["link_count"] = len(source["tree"]["links"])
        for node in source["tree"]["nodes"].values():
            node["layout"].update(location=node["layout"]["absolute_location"], bounds=None, bounds_status="unavailable")
        schema = json.loads((ROOT / "schemas/node-layout-v1.json").read_text())
        jsonschema.validate(source, schema)
        self.assertEqual(plan_layout(source)["status"], "blocked")


class LayoutToolTests(unittest.TestCase):
    def test_tool_forwarding_and_workspace_output(self):
        from blender_mcp.tools import layout
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"BLENDER_MCP_WORKSPACE": directory}), patch.object(layout, "get_blender_connection") as connection:
            connection.return_value.send_command.return_value = sample()
            result = json.loads(layout.inspect_node_layout(None, sample()["tree_ref"], output_path="layout.json"))
            self.assertEqual(result["status"], "written")
            self.assertEqual(json.loads(Path(result["path"]).read_text(encoding="utf-8")), sample())
            with self.assertRaises(ValueError):
                layout.inspect_node_layout(None, sample()["tree_ref"], output_path="../outside.json")
            layout.plan_node_layout(None, sample()["tree_ref"], sample()["revision"], main_path=["Output"])
            args = connection.return_value.send_command.call_args.args
            self.assertEqual(args[0], "plan_node_layout")
            self.assertEqual(args[1]["main_path"], ["Output"])


if __name__ == "__main__":
    unittest.main()
