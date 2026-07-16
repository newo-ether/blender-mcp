from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sample_query(query_type: str, records: list[dict]) -> dict:
    query = {
        "node_names": [],
        "from_node": "",
        "to_node": "",
        "attribute_name": "",
        "socket_id": "",
        "direction": "downstream",
        "fields": [],
        "limit": 200,
    }
    if query_type == "fields":
        query["fields"] = ["bl_idname", "name"]
    elif query_type == "shortest_path":
        query["from_node"] = "Source"
        query["to_node"] = "Target"
    elif query_type in {"upstream", "downstream", "slice"}:
        query["node_names"] = ["Target"]
        query["direction"] = query_type if query_type != "slice" else "both"
    return {
        "schema": "blender-node-graph-query/1",
        "tree_ref": {
            "tree_type": "GeometryNodeTree",
            "owner": {"kind": "NODE_GROUP", "name": "Graph"},
        },
        "revision": "sha256:" + "a" * 64,
        "query_type": query_type,
        "query": query,
        "total_matches": len(records),
        "truncated": False,
        "records": records,
    }


class NodeGraphQuerySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from jsonschema import Draft202012Validator
        except ImportError as exc:
            raise unittest.SkipTest("jsonschema is not installed") from exc
        cls.schema = json.loads(
            (ROOT / "schemas" / "node-graph-query-v1.json").read_text(
                encoding="utf-8"
            )
        )
        cls.validator = Draft202012Validator(cls.schema)

    def test_every_query_record_shape_validates(self):
        link = {
            "from_node": "Source",
            "from_socket": "output:0:Value",
            "to_node": "Target",
            "to_socket": "input:0:Value",
        }
        cases = {
            "fields": [{"name": "Source", "bl_idname": "ShaderNodeValue"}],
            "socket_links": [link],
            "named_attributes": [{
                "node": "Read Name",
                "node_type": "GeometryNodeInputNamedAttribute",
                "attribute": "velocity",
                "access": "reader",
                "data_type": "FLOAT_VECTOR",
            }],
            "shortest_path": [link],
            "upstream": [{"node": "Source", "node_type": "ShaderNodeValue"}],
            "downstream": [{"node": "Target", "node_type": "ShaderNodeMath"}],
            "slice": [{"node": "Target", "node_type": "ShaderNodeMath"}],
        }
        for query_type, records in cases.items():
            with self.subTest(query_type=query_type):
                self.validator.validate(sample_query(query_type, records))

    def test_query_and_record_fields_fail_closed(self):
        invalid_field = sample_query("fields", [{"name": "Source"}])
        invalid_field["query"]["fields"] = ["not_a_field"]
        self.assertTrue(list(self.validator.iter_errors(invalid_field)))

        invalid_record = sample_query("socket_links", [{
            "from_node": "Source",
            "from_socket": "output:0:Value",
            "to_node": "Target",
            "to_socket": "input:0:Value",
            "unexpected": True,
        }])
        self.assertTrue(list(self.validator.iter_errors(invalid_record)))


if __name__ == "__main__":
    unittest.main()
