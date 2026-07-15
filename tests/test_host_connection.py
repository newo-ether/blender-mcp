from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from blender_mcp import host
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


class HostConnectionTests(unittest.TestCase):
    def setUp(self):
        self.previous_connection = host.blender_connection
        self.previous_active = host.instance_manager.active
        host.blender_connection = None
        host.instance_manager.active = None
        _FakeConnection.created.clear()

    def tearDown(self):
        host.blender_connection = self.previous_connection
        host.instance_manager.active = self.previous_active

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


if __name__ == "__main__":
    unittest.main()
