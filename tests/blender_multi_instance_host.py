"""Run one GUI Blender bridge for the simultaneous multi-instance acceptance."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys
import time

import bpy


def parse_args():
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args(arguments)


def load_addon(path):
    spec = importlib.util.spec_from_file_location("blender_mcp_multi_host", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load add-on: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    args = parse_args()
    os.environ["BLENDER_MCP_RUNTIME_DIR"] = str(Path(args.runtime_dir).resolve())
    addon = load_addon(Path(args.addon).resolve())
    addon.register()
    bpy.context.scene.name = f"MCP {args.label}"
    bpy.context.scene["multi_instance_label"] = args.label
    server = addon.BlenderMCPServer()
    bpy.types.blendermcp_server = server
    server.start()
    if not server.running or not server.socket:
        raise RuntimeError("Blender MCP bridge failed to start")
    if addon._BLENDER_MCP_OVERLAY_HANDLE is None:
        raise RuntimeError("AI occupancy overlay handler was not registered")

    deadline = time.monotonic() + args.timeout
    stop_file = Path(args.stop_file)

    def shutdown_tick():
        if not stop_file.exists() and time.monotonic() < deadline:
            return 0.2
        try:
            addon.unregister()
        finally:
            bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(shutdown_tick, first_interval=0.2)
    print(f"BLENDER_MCP_MULTI_HOST={args.label}")


if __name__ == "__main__":
    main()
