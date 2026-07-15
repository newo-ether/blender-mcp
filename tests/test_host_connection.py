from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from blender_mcp import host
from blender_mcp.protocol.errors import BlenderMCPError
from blender_mcp.transport.connection import BlenderConnection
from blender_mcp.transport.constants import DEFAULT_BRIDGE_HOST, DEFAULT_BRIDGE_PORT


class _FakeConnection:
    created = []

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.connected = False
        self.params_enricher = None
        self.created.append(self)

    def connect(self):
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def send_command(self, command, params=None):
        if command == "get_polyhaven_status":
            return {"enabled": False}
        return {"ok": True}


class _RecoveringManager:
    def __init__(self, old_connection, new_connection):
        self.active = old_connection
        self.active_record = {
            "instance_id": "instance-one",
            "file_session_id": "file-one",
        }
        self.new_connection = new_connection
        self.directory = None
        self.claimed = []
        self.invalidated = False

    def ensure_lease(self):
        raise BlenderMCPError(
            "mcp_transport_error",
            "old socket closed",
            retryable=True,
        )

    def claim(self, instance_id, *, expected_file_session_id="", lease_seconds=120):
        self.claimed.append((instance_id, expected_file_session_id, lease_seconds))
        self.active.disconnect()
        self.active = self.new_connection
        self.active.connect()
        return {"active": True}

    def prepare_params(self, command, params):
        return dict(params or {})

    def invalidate(self):
        self.invalidated = True
        if self.active is not None:
            self.active.disconnect()
        self.active = None
        self.active_record = None


class _ClosingSocket:
    def __init__(self):
        self.closed = False

    def sendall(self, _data):
        return None

    def settimeout(self, _seconds):
        return None

    def recv(self, _size):
        return b""

    def shutdown(self, _how):
        return None

    def close(self):
        self.closed = True


class HostConnectionTests(unittest.TestCase):
    def setUp(self):
        self.previous_connection = host.blender_connection
        self.previous_manager_state = (
            host.instance_manager.active,
            host.instance_manager.active_record,
            host.instance_manager.claim_token,
            host.instance_manager.claim_expires_at,
        )
        host.blender_connection = None
        host.instance_manager.active = None
        host.instance_manager.active_record = None
        host.instance_manager.claim_token = ""
        host.instance_manager.claim_expires_at = 0.0
        _FakeConnection.created.clear()

    def tearDown(self):
        host.blender_connection = self.previous_connection
        (
            host.instance_manager.active,
            host.instance_manager.active_record,
            host.instance_manager.claim_token,
            host.instance_manager.claim_expires_at,
        ) = self.previous_manager_state

    def test_fallback_uses_named_loopback_defaults(self):
        with (
            patch.object(host, "BlenderConnection", _FakeConnection),
            patch.object(host, "discover_registry_records", return_value=[]),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("BLENDER_HOST", None)
            os.environ.pop("BLENDER_PORT", None)
            connection = host.get_blender_connection()
        self.assertEqual(connection.host, DEFAULT_BRIDGE_HOST)
        self.assertEqual(connection.port, DEFAULT_BRIDGE_PORT)
        self.assertTrue(connection.connected)

    def test_explicit_endpoint_override_is_preserved(self):
        with (
            patch.object(host, "BlenderConnection", _FakeConnection),
            patch.object(host, "discover_registry_records", return_value=[]),
            patch.dict(
                os.environ,
                {"BLENDER_HOST": "localhost", "BLENDER_PORT": "19876"},
            ),
        ):
            connection = host.get_blender_connection()
        self.assertEqual((connection.host, connection.port), ("localhost", 19876))

    def test_server_startup_does_not_claim_blender(self):
        async def exercise_lifespan():
            with (
                patch.object(host, "record_startup"),
                patch.object(host, "get_blender_connection") as connect,
                patch.object(host, "release_blender_connection"),
            ):
                async with host.server_lifespan(host.mcp):
                    connect.assert_not_called()

        asyncio.run(exercise_lifespan())

    def test_renew_failure_reconnects_the_exact_selected_instance_once(self):
        old_connection = _FakeConnection("127.0.0.1", 10001)
        old_connection.connect()
        new_connection = _FakeConnection("127.0.0.1", 10001)
        manager = _RecoveringManager(old_connection, new_connection)
        host.blender_connection = old_connection
        with patch.object(host, "instance_manager", manager):
            connection = host.get_blender_connection()
        self.assertIs(connection, new_connection)
        self.assertEqual(manager.claimed, [("instance-one", "file-one", 120.0)])
        self.assertFalse(old_connection.connected)
        self.assertTrue(new_connection.connected)

    def test_eof_is_a_retryable_transport_error_and_closes_socket(self):
        sock = _ClosingSocket()
        connection = BlenderConnection("127.0.0.1", 10002, sock=sock)
        with self.assertRaises(BlenderMCPError) as captured:
            connection.send_command("export_node_tree")
        self.assertEqual(captured.exception.code, "mcp_transport_error")
        self.assertTrue(captured.exception.retryable)
        self.assertEqual(captured.exception.details["operation"], "export_node_tree")
        self.assertTrue(sock.closed)
        self.assertIsNone(connection.sock)


if __name__ == "__main__":
    unittest.main()
