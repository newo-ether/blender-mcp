from __future__ import annotations

import runpy
import unittest
from pathlib import Path

AUDIT_PATH = Path(__file__).resolve().parents[1] / "skills/blender-mcp/scripts/audit_node_frames.py"
audit_snapshot = runpy.run_path(str(AUDIT_PATH))["audit_snapshot"]


def node(name, kind="ShaderNodeMath", parent=None):
    return {"name": name, "bl_idname": kind, "layout": {"parent": parent}}


def snapshot(*nodes):
    return {
        "view": "layout",
        "scope": {"kind": "full"},
        "tree": {"name": "Example", "nodes": {n["name"]: n for n in nodes}},
        "stats": {"node_count": len(nodes), "total_node_count": len(nodes)},
    }


class NodeFrameAuditTests(unittest.TestCase):
    def test_four_and_nineteen_children_are_accepted(self):
        for count in (4, 19):
            with self.subTest(count=count):
                source = snapshot(node("Stage", "NodeFrame"), *[
                    node(f"Operation.{i}", parent="Stage") for i in range(count)
                ])
                result = audit_snapshot(source)
                self.assertTrue(result["valid"])
                self.assertFalse(result["visual_layout_checked"])

    def test_nested_frames_count_as_one_child_and_are_audited_separately(self):
        nodes = [node("Assembly", "NodeFrame"), node("Output", "NodeGroupOutput", "Assembly")]
        for i in range(4):
            name = f"Stage.{i}"
            nodes.append(node(name, "NodeFrame", "Assembly"))
            nodes.extend(node(f"Op.{i}.{j}", parent=name) for j in range(4))
        result = audit_snapshot(snapshot(*nodes))
        self.assertTrue(result["valid"])
        self.assertEqual(result["frames"][0]["direct_child_count"], 5)
        self.assertEqual(result["functional_node_count"], 17)

    def test_three_twenty_and_empty_frames_fail(self):
        for count in (0, 3, 20):
            with self.subTest(count=count):
                result = audit_snapshot(snapshot(node("Stage", "NodeFrame"), *[
                    node(f"Op.{i}", parent="Stage") for i in range(count)
                ]))
                self.assertFalse(result["valid"])
                self.assertEqual(len(result["undersized_frames"] if count < 4 else result["oversized_frames"]), 1)

    def test_inputs_outputs_and_reroutes_have_no_membership_exemption(self):
        loose = [node("Input", "NodeGroupInput"), node("Output", "NodeGroupOutput"), node("Wire", "NodeReroute")]
        result = audit_snapshot(snapshot(*loose))
        self.assertFalse(result["valid"])
        self.assertEqual(result["unframed_nodes"], ["Input", "Output", "Wire"])

    def test_missing_and_non_frame_parents_fail(self):
        result = audit_snapshot(snapshot(node("Op"), node("Bad", parent="Op"), node("Missing", parent="Absent")))
        self.assertFalse(result["valid"])
        self.assertEqual(len(result["invalid_parents"]), 2)

    def test_nested_parent_cycle_fails(self):
        nodes = [node("A", "NodeFrame", "B"), node("B", "NodeFrame", "A")]
        for parent in ("A", "B"):
            nodes.extend(node(f"{parent}.{i}", parent=parent) for i in range(3))
        result = audit_snapshot(snapshot(*nodes))
        self.assertFalse(result["valid"])
        self.assertEqual(result["parent_cycles"], [["A", "B"]])

    def test_incomplete_or_non_layout_exports_are_rejected(self):
        cases = [snapshot(node("Input", "NodeGroupInput")) for _ in range(4)]
        cases[0]["scope"]["kind"] = "subgraph"
        cases[1]["stats"]["total_node_count"] = 2
        cases[2]["view"] = "slim"
        del cases[3]["tree"]["nodes"]["Input"]["layout"]["parent"]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(ValueError):
                audit_snapshot(source)


if __name__ == "__main__":
    unittest.main()
