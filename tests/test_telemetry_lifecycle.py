from __future__ import annotations

import unittest
from unittest.mock import patch

from blender_mcp import host
from blender_mcp.observability.telemetry import TelemetryCollector


class TelemetryLifecycleTests(unittest.TestCase):
    def test_consent_probe_never_creates_a_blender_claim(self):
        collector = TelemetryCollector.__new__(TelemetryCollector)
        previous_connection = host.blender_connection
        host.blender_connection = None
        try:
            with patch.object(host, "get_blender_connection") as connect:
                self.assertFalse(collector._check_user_consent())
                connect.assert_not_called()
        finally:
            host.blender_connection = previous_connection


if __name__ == "__main__":
    unittest.main()
