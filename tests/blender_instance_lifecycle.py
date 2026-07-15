"""Live Blender check that discovery heartbeats survive file resets."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import bpy


def parse_args():
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", required=True)
    return parser.parse_args(arguments)


def load_addon(path):
    spec = importlib.util.spec_from_file_location("blender_mcp_lifecycle_acceptance", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load add-on: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def main():
    addon = load_addon(Path(parse_args().addon).resolve())
    server = addon.BlenderMCPServer()
    server.running = True
    try:
        handshake = server.execute_command(
            {"type": "blender_mcp_handshake", "params": {}}
        )
        assert handshake["status"] == "success", handshake
        assert not handshake["result"]["busy"], (
            "the discovery handshake must not mark an idle instance busy"
        )
        assert server.ensure_heartbeat_timer()
        assert bpy.app.timers.is_registered(server._heartbeat_callback)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        assert bpy.app.timers.is_registered(server._heartbeat_callback), (
            "heartbeat timer was removed by a Blender file reset"
        )
    finally:
        server.running = False
        if bpy.app.timers.is_registered(server._heartbeat_callback):
            bpy.app.timers.unregister(server._heartbeat_callback)
    print("BLENDER_MCP_INSTANCE_LIFECYCLE=persistent-heartbeat")


if __name__ == "__main__":
    main()
