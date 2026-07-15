"""Launch two GUI Blenders and prove discovery, ambiguity, and mutation isolation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender_mcp.protocol.errors import BlenderMCPError  # noqa: E402
from blender_mcp.transport.connection import BlenderConnection  # noqa: E402
from blender_mcp.transport.instances import (  # noqa: E402
    InstanceConnectionManager,
    discover_registry_records,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", action="append", required=True)
    parser.add_argument(
        "--addon",
        default=str(ROOT / "blender_extension" / "__init__.py"),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def wait_until(predicate, timeout, label):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {label}")


def execute_probe(manager, value):
    manager.active.params_enricher = manager.prepare_params
    return manager.active.send_command(
        "execute_code",
        {"code": f"bpy.context.scene['isolated_probe'] = {value!r}; print({value!r})"},
    )


def read_probe(manager):
    manager.active.params_enricher = manager.prepare_params
    return manager.active.send_command(
        "execute_code",
        {"code": "print(bpy.context.scene.get('isolated_probe', 'missing'))"},
    )["result"].strip()


def main():
    args = parse_args()
    if len(args.blender) != 2:
        raise ValueError("Pass exactly two --blender executables")
    host_script = Path(__file__).with_name("blender_multi_instance_host.py")
    processes = []
    state_root_value = os.environ.get("BLENDER_MCP_TEST_STATE_ROOT")
    state_root = Path(state_root_value).resolve() if state_root_value else None
    if state_root is not None:
        state_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="blender-mcp-multi-", dir=state_root
    ) as directory:
        directory_path = Path(directory)
        runtime = Path(directory) / "registry"
        runtime.mkdir()
        stop_files = [Path(directory) / f"stop-{index}" for index in range(2)]
        log_paths = [directory_path / f"blender-{index}.log" for index in range(2)]
        log_handles = []
        failure = None
        try:
            for index, blender in enumerate(args.blender):
                isolated_config = directory_path / f"config-{index}"
                isolated_config.mkdir()
                environment = os.environ.copy()
                environment["BLENDER_USER_CONFIG"] = str(isolated_config)
                log_handle = log_paths[index].open("w", encoding="utf-8")
                log_handles.append(log_handle)
                command = [
                    str(Path(blender).resolve()),
                    "--factory-startup",
                    "--disable-autoexec",
                    "--python",
                    str(host_script),
                    "--",
                    "--addon",
                    str(Path(args.addon).resolve()),
                    "--runtime-dir",
                    str(runtime),
                    "--label",
                    f"Window {index + 1}",
                    "--stop-file",
                    str(stop_files[index]),
                    "--timeout",
                    str(args.timeout + 20),
                ]
                processes.append(subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ))

            discovered = wait_until(
                lambda: (
                    records
                    if len(records := discover_registry_records(directory=runtime)) == 2
                    and all(item.status == "ready" for item in records)
                    else None
                ),
                args.timeout,
                "two ready Blender registrations",
            )
            raw_records = [item.record for item in discovered]
            assert not list(runtime.glob("*.tmp")), "Registry publication left temporary files behind"
            assert len({item["instance_id"] for item in raw_records}) == 2
            assert len({item["file_session_id"] for item in raw_records}) == 2
            assert len({item["port"] for item in raw_records}) == 2

            manager = InstanceConnectionManager(
                connection_factory=lambda host, port: BlenderConnection(host=host, port=port),
                directory=runtime,
                owner_label="Multi-instance acceptance",
            )
            public = manager.list_instances(validate_live=True)
            assert len(public) == 2
            assert all(item["status"] == "ready" for item in public), public
            assert all("port" not in item for item in public)
            try:
                manager.auto_select()
            except BlenderMCPError as error:
                assert error.code == "multiple_instances_require_selection"
            else:
                raise AssertionError("Ambiguous instances were selected automatically")

            ordered = sorted(public, key=lambda item: item["active_scene"])
            first, second = ordered
            manager.claim(first["instance_id"], expected_file_session_id=first["file_session_id"])
            assert manager.active_record["active_scene"] == first["active_scene"]
            execute_probe(manager, "first-only")

            second_raw = next(item for item in raw_records if item["instance_id"] == second["instance_id"])
            unclaimed = BlenderConnection(second_raw["host"], second_raw["port"])
            assert unclaimed.connect()
            try:
                unclaimed.send_command("execute_code", {"code": "print('must not run')"})
            except BlenderMCPError as error:
                assert error.code == "claim_expired"
            else:
                raise AssertionError("A mutation without a claim was accepted")
            finally:
                unclaimed.disconnect()

            manager.claim(second["instance_id"], expected_file_session_id=second["file_session_id"])
            execute_probe(manager, "second-only")
            manager.claim(first["instance_id"], expected_file_session_id=first["file_session_id"])
            assert read_probe(manager) == "first-only"
            manager.claim(second["instance_id"], expected_file_session_id=second["file_session_id"])
            assert read_probe(manager) == "second-only"
            manager.release()

            print("BLENDER_MCP_MULTI_INSTANCE=" + json.dumps({
                "instances": len(public),
                "distinct_endpoints": True,
                "public_ports": False,
                "ambiguous_auto_selection": False,
                "wrong_target_mutations": 0,
                "isolated_values": ["first-only", "second-only"],
            }, sort_keys=True))
        except Exception as error:
            failure = error
        finally:
            for stop_file in stop_files:
                stop_file.touch(exist_ok=True)
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
            for log_handle in log_handles:
                log_handle.close()
        if failure is not None:
            diagnostics = []
            for index, log_path in enumerate(log_paths):
                output = log_path.read_text(encoding="utf-8", errors="replace")
                diagnostics.append(f"Blender {index + 1}:\n{output[-8000:]}")
            raise RuntimeError(
                f"{failure}\n\n" + "\n\n".join(diagnostics)
            ) from failure


if __name__ == "__main__":
    main()
