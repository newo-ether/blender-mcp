"""Run the portable, preference-isolated Blender acceptance matrix."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from blender_test_runtime import DEFAULT_RUNTIME_ROOT, ensure_runtime

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "blender_extension" / "__init__.py"
TESTS = ROOT / "tests"
ALL_VERSIONS = ("4.2", "5.1", "5.2")
ALL_SUITES = ("smoke", "core", "improve", "multi", "extension")
CORE_CASES = (
    ("node-bootstrap", "blender_node_bootstrap.py"),
    ("compositor-initialization", "blender_compositor_initialization.py"),
    ("compositor-transactions", "blender_compositor_nodes_transactions.py"),
    ("geometry-linked", "blender_geometry_nodes_linked.py"),
    ("geometry-readonly", "blender_geometry_nodes_readonly.py"),
    ("geometry-scale", "blender_geometry_nodes_scale.py"),
    ("instance-lifecycle", "blender_instance_lifecycle.py"),
    ("node-corners", "blender_node_tree_corner_cases.py"),
    ("node-editor-context", "blender_node_editor_context.py"),
    ("node-efficiency", "blender_node_tree_model_efficiency.py"),
    ("node-validation", "blender_node_tree_validation.py"),
    ("node-readonly", "blender_node_trees_readonly.py"),
    ("shader-compositor-capabilities", "blender_shader_compositor_capabilities.py"),
    ("shader-compositor-dynamic", "blender_shader_compositor_dynamic.py"),
    ("shader-compositor-linked", "blender_shader_compositor_linked.py"),
    ("shader-compositor-scale", "blender_shader_compositor_scale.py"),
    ("shader-compositor-transactions", "blender_shader_compositor_transactions.py"),
    ("shader-transactions", "blender_shader_nodes_transactions.py"),
    ("version-context", "blender_version_context.py"),
)


def parse_override(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "Use VERSION=PATH, for example 5.2=/opt/blender"
        )
    version, raw_path = value.split("=", 1)
    if version not in ALL_VERSIONS:
        raise argparse.ArgumentTypeError(f"Unsupported version alias: {version}")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Blender executable does not exist: {path}")
    return version, path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download pinned portable Blender releases into .test-runtime, "
            "isolate all preferences/state, and run live acceptance tests."
        )
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=(*ALL_SUITES, "all"),
        help="Acceptance suite to run; repeat as needed (default: all)",
    )
    parser.add_argument(
        "--version",
        action="append",
        choices=ALL_VERSIONS,
        help="Version used by smoke/core/extension tests; repeat as needed (default: all)",
    )
    parser.add_argument(
        "--blender",
        action="append",
        default=[],
        type=parse_override,
        metavar="VERSION=PATH",
        help="Explicit offline executable override; never discovered implicitly",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Ignored runtime/cache root (default: .test-runtime)",
    )
    parser.add_argument("--offline", action="store_true", help="Forbid downloads")
    parser.add_argument("--keep-state", action="store_true", help="Keep per-run state")
    return parser.parse_args()


def isolated_environment(case_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    paths = {
        "BLENDER_USER_CONFIG": case_root / "config",
        "BLENDER_USER_DATAFILES": case_root / "datafiles",
        "BLENDER_USER_SCRIPTS": case_root / "scripts",
        "BLENDER_USER_EXTENSIONS": case_root / "extensions",
        "BLENDER_MCP_RUNTIME_DIR": case_root / "registry",
        "TEMP": case_root / "temp",
        "TMP": case_root / "temp",
        "TMPDIR": case_root / "temp",
    }
    for variable, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(path)
    environment["BLENDER_MCP_TEST_STATE_ROOT"] = str(case_root / "children")
    Path(environment["BLENDER_MCP_TEST_STATE_ROOT"]).mkdir(parents=True, exist_ok=True)
    source = str(ROOT / "src")
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


def run_logged(
    name: str,
    command: list[str],
    *,
    environment: dict[str, str],
    log_root: Path,
    timeout: int = 300,
) -> None:
    log_path = log_root / f"{name}.log"
    print(f"[{name}] {' '.join(command)}")
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    output = log_path.read_text(encoding="utf-8", errors="replace")
    traceback_found = "Traceback (most recent call last):" in output
    if result.returncode != 0 or traceback_found:
        print(output[-12000:], file=sys.stderr)
        reason = (
            f"exit code {result.returncode}"
            if result.returncode != 0
            else "a Python traceback despite exit code 0"
        )
        raise RuntimeError(
            f"{name} failed with {reason}; see {log_path}"
        )
    result_lines = [
        line
        for line in output.splitlines()
        if line.startswith("BLENDER_MCP_") or line.startswith("Built and validated:")
    ]
    for line in result_lines:
        print(f"[{name}] {line}")


def blender_background(executable: Path, script: Path, *arguments: str) -> list[str]:
    return [
        str(executable),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python",
        str(script),
        "--",
        *arguments,
    ]


def required_versions(suites: Iterable[str], smoke_versions: Iterable[str]) -> set[str]:
    required = set(
        smoke_versions if {"smoke", "core", "extension"}.intersection(suites) else ()
    )
    if "improve" in suites:
        required.add("5.2")
    if "multi" in suites:
        required.update(("5.1", "5.2"))
    return required


def main() -> int:
    args = parse_args()
    suites = set(args.suite or ("all",))
    if "all" in suites:
        suites = set(ALL_SUITES)
    smoke_versions = tuple(args.version or ALL_VERSIONS)
    runtime_root = args.runtime_root.expanduser().resolve()
    overrides = dict(args.blender)
    runtimes: dict[str, Path] = {}
    for version in sorted(required_versions(suites, smoke_versions)):
        runtimes[version] = overrides.get(version) or ensure_runtime(
            version, runtime_root=runtime_root, offline=args.offline
        )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    state_root = runtime_root / "state" / run_id
    log_root = runtime_root / "logs" / run_id
    state_root.mkdir(parents=True)
    log_root.mkdir(parents=True)
    completed: list[str] = []
    try:
        if "smoke" in suites:
            for version in smoke_versions:
                name = f"smoke-{version}"
                run_logged(
                    name,
                    blender_background(
                        runtimes[version],
                        TESTS / "blender_runtime_knowledge.py",
                        "--addon",
                        str(ADDON),
                    ),
                    environment=isolated_environment(state_root / name),
                    log_root=log_root,
                )
                completed.append(name)

        if "core" in suites:
            for version in smoke_versions:
                for case_name, script_name in CORE_CASES:
                    name = f"core-{version}-{case_name}"
                    arguments: tuple[str, ...] = ()
                    if script_name in {
                        "blender_node_bootstrap.py",
                        "blender_compositor_initialization.py",
                        "blender_instance_lifecycle.py",
                    }:
                        arguments = ("--addon", str(ADDON))
                    elif script_name == "blender_version_context.py":
                        arguments = (
                            "--addon",
                            str(ADDON),
                            "--resolver",
                            str(
                                ROOT
                                / "src"
                                / "blender_mcp"
                                / "documentation"
                                / "context.py"
                            ),
                        )
                    run_logged(
                        name,
                        blender_background(
                            runtimes[version],
                            TESTS / script_name,
                            *arguments,
                        ),
                        environment=isolated_environment(state_root / name),
                        log_root=log_root,
                    )
                    completed.append(name)

        if "improve" in suites:
            name = "improve-5.2"
            run_logged(
                name,
                blender_background(
                    runtimes["5.2"],
                    TESTS / "blender_improve_multi_instance.py",
                    "--addon",
                    str(ADDON),
                ),
                environment=isolated_environment(state_root / name),
                log_root=log_root,
            )
            completed.append(name)

        if "multi" in suites:
            name = "multi-5.1-5.2"
            run_logged(
                name,
                [
                    sys.executable,
                    str(TESTS / "multi_instance_acceptance.py"),
                    "--blender",
                    str(runtimes["5.1"]),
                    "--blender",
                    str(runtimes["5.2"]),
                    "--timeout",
                    "45",
                ],
                environment=isolated_environment(state_root / name),
                log_root=log_root,
            )
            completed.append(name)

        if "extension" in suites:
            for version in smoke_versions:
                name = f"extension-{version}"
                output_dir = runtime_root / "artifacts" / run_id / version
                run_logged(
                    name,
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "build_blender_extension.py"),
                        "--blender",
                        str(runtimes[version]),
                        "--output-dir",
                        str(output_dir),
                    ],
                    environment=isolated_environment(state_root / name),
                    log_root=log_root,
                )
                completed.append(name)
    finally:
        if not args.keep_state:
            shutil.rmtree(state_root, ignore_errors=True)

    print(f"Portable Blender acceptance passed: {', '.join(completed)}")
    print(f"Logs: {log_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
