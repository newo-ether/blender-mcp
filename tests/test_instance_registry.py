from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.errors import BlenderMCPError  # noqa: E402
from blender_mcp.instance_registry import (  # noqa: E402
    INSTANCE_PROTOCOL,
    REGISTRY_SCHEMA,
    InstanceConnectionManager,
    discover_registry_records,
)


class FakeConnection:
    instances = {}

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.connected = False
        self.params_enricher = None

    def connect(self):
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def send_command(self, command, params=None):
        state = self.instances[self.port]
        if command == "blender_mcp_handshake":
            return {
                "instance_id": state["instance_id"],
                "file_session_id": state["file_session_id"],
                "protocol_version": INSTANCE_PROTOCOL,
            }
        if command == "claim_blender_instance":
            state["claimed_by"] = params["client_id"]
            return {"claim_token": "secret-token", "expires_at": time.time() + 120}
        if command == "renew_blender_instance":
            state["renewals"] = state.get("renewals", 0) + 1
            return {"renewed": True, "expires_at": time.time() + params["lease_seconds"]}
        if command == "release_blender_instance":
            state["claimed_by"] = ""
            return {"released": True}
        return {"ok": True}


def write_record(root: Path, instance_id: str, port: int, **values):
    record = {
        "schema": REGISTRY_SCHEMA,
        "protocol_version": INSTANCE_PROTOCOL,
        "instance_id": instance_id,
        "file_session_id": f"file-{instance_id}",
        "pid": 123,
        "host": "127.0.0.1",
        "port": port,
        "heartbeat_at": time.time(),
        "blender_version": "5.2.0",
        "binary_path": "blender.exe",
        "blend_file": "",
        "dirty": False,
        "active_scene": "Scene",
        "addon_version": "1.11.0",
        "allow_ai_control": True,
        "busy": False,
        "claim": None,
    }
    record.update(values)
    (root / f"{instance_id}.json").write_text(json.dumps(record), encoding="utf-8")
    FakeConnection.instances[port] = record
    return record


class InstanceRegistryTests(unittest.TestCase):
    def test_discovers_ready_manual_and_stale_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_record(root, "ready", 10001)
            write_record(root, "manual", 10002, allow_ai_control=False)
            write_record(root, "stale", 10003, heartbeat_at=time.time() - 60)
            states = {item.record["instance_id"]: item.status for item in discover_registry_records(directory=root)}
            self.assertEqual(states, {"manual": "manual", "ready": "ready", "stale": "stale"})

    def test_ambiguous_auto_selection_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_record(root, "one", 10011)
            write_record(root, "two", 10012)
            manager = InstanceConnectionManager(FakeConnection, directory=root)
            with self.assertRaises(BlenderMCPError) as captured:
                manager.auto_select()
            self.assertEqual(captured.exception.code, "multiple_instances_require_selection")

    def test_claim_enrich_and_release_keep_token_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_record(root, "one", 10021)
            manager = InstanceConnectionManager(FakeConnection, directory=root, owner_label="Codex")
            summary = manager.claim("one", expected_file_session_id="file-one")
            self.assertTrue(summary["active"])
            self.assertNotIn("claim_token", summary)
            prepared = manager.prepare_params("apply_node_tree_patch", {"patch": {}})
            self.assertEqual(prepared["_instance_id"], "one")
            self.assertEqual(prepared["_claim_token"], "secret-token")
            self.assertTrue(manager.release()["released"])
            self.assertFalse(manager.active_summary()["active"])

    def test_active_claim_renews_before_expiry_and_invalidate_forgets_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = write_record(root, "one", 10031)
            manager = InstanceConnectionManager(FakeConnection, directory=root)
            manager.claim("one", lease_seconds=30)
            manager.claim_expires_at = time.time() + 1
            manager.ensure_lease()
            self.assertEqual(record["renewals"], 1)
            self.assertGreater(manager.claim_expires_at, time.time() + 100)
            manager.invalidate()
            self.assertFalse(manager.active_summary()["active"])
            self.assertEqual(manager.claim_token, "")


if __name__ == "__main__":
    unittest.main()
