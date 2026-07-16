from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_PRODUCTION_MODULE_LINES = 1000
SOURCE_SUFFIXES = {".cmd", ".json", ".ps1", ".py", ".toml", ".yaml", ".yml"}
SOURCE_ROOTS = (
    ROOT / ".github",
    ROOT / "blender_extension",
    ROOT / "packaging",
    ROOT / "scripts",
    ROOT / "src",
    ROOT / "tests",
)


def repository_source_files():
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix.casefold() in SOURCE_SUFFIXES:
            yield path
    for source_root in SOURCE_ROOTS:
        for path in source_root.rglob("*"):
            if path.is_file() and path.suffix.casefold() in SOURCE_SUFFIXES:
                yield path


def relative_import_graph(package_root: Path, package_name: str):
    modules = {}
    for path in package_root.rglob("*.py"):
        parts = list(path.relative_to(package_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module_name = ".".join((package_name, *parts)).rstrip(".")
        modules[module_name] = path

    graph = {module_name: set() for module_name in modules}
    for module_name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = (
            module_name
            if path.name == "__init__.py"
            else module_name.rpartition(".")[0]
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            package_parts = package.split(".")
            target_parts = package_parts[: len(package_parts) - node.level + 1]
            if node.module:
                target_parts.extend(node.module.split("."))
            target = ".".join(target_parts)
            children = {
                f"{target}.{alias.name}"
                for alias in node.names
                if f"{target}.{alias.name}" in modules
            }
            if children:
                graph[module_name].update(children)
            elif target in modules:
                graph[module_name].add(target)
    return graph


def import_cycles(graph):
    visiting = []
    visited = set()
    cycles = set()

    def visit(module_name):
        if module_name in visiting:
            start = visiting.index(module_name)
            cycle = visiting[start:] + [module_name]
            rotations = [
                tuple(cycle[index:-1] + cycle[:index] + [cycle[index]])
                for index in range(len(cycle) - 1)
            ]
            cycles.add(min(rotations))
            return
        if module_name in visited:
            return
        visiting.append(module_name)
        for dependency in sorted(graph[module_name]):
            visit(dependency)
        visiting.pop()
        visited.add(module_name)

    for module_name in sorted(graph):
        visit(module_name)
    return sorted(cycles)


class RepositoryStructureTests(unittest.TestCase):
    def test_repository_code_does_not_depend_on_agent_execution_files(self):
        forbidden = "." + "codex"
        allowed_installer_references = {
            "scripts/installer/discovery.ps1",
            "tests/test_installer_targets_and_mcpb.ps1",
        }
        references = [
            path.relative_to(ROOT).as_posix()
            for path in repository_source_files()
            if path != Path(__file__).resolve()
            and forbidden in path.read_text(encoding="utf-8", errors="replace")
        ]
        # The Windows installer now supports Codex Desktop without Codex CLI,
        # so only its config-path resolver and focused contract test may name
        # the shared per-user Codex directory.
        self.assertEqual(set(references), allowed_installer_references)

    def test_repository_code_has_no_user_specific_absolute_paths(self):
        patterns = (
            re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\"']+", re.IGNORECASE),
            re.compile(r"/Users/[^/\"']+"),
            re.compile(r"/home/[^/\"']+"),
        )
        references = []
        for path in repository_source_files():
            if path == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(text) for pattern in patterns):
                references.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(references, [])

    def test_scratch_outputs_are_ignored(self):
        ignore_lines = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        }
        self.assertTrue(
            {
                "__pycache__/",
                "*.py[oc]",
                "build/",
                "dist/",
                "wheels/",
                "*.egg-info",
                ".venv",
                ".test-runtime/",
                ".tmp*/",
                "*.tmp",
                "*.log",
                ".coverage",
                "htmlcov/",
                ".pytest_cache/",
                ".mypy_cache/",
                ".ruff_cache/",
            }.issubset(ignore_lines)
        )

    def test_agent_directory_contains_only_plans_and_execution_logs(self):
        agent_root = ROOT / ("." + "codex")
        if not agent_root.exists():
            return
        generated = [
            path.relative_to(ROOT).as_posix()
            for path in agent_root.rglob("*")
            if path.is_file() and path.suffix.casefold() != ".md"
        ]
        self.assertEqual(generated, [])

    def test_tracked_root_garbage_is_absent(self):
        self.assertFalse((ROOT / ".DS_Store").exists())
        garbage = [
            path.name
            for path in ROOT.iterdir()
            if path.is_file()
            and path.suffix.casefold() in {".bak", ".log", ".orig", ".rej", ".tmp"}
        ]
        self.assertEqual(garbage, [])

    def test_root_implementation_monoliths_are_absent(self):
        for relative_path in (
            "addon.py",
            "blender_mcp_addon_runtime.py",
            "main.py",
        ):
            with self.subTest(path=relative_path):
                self.assertFalse((ROOT / relative_path).exists())

    def test_legacy_flat_mcp_modules_are_absent(self):
        for relative_path in (
            "src/blender_mcp/blender_docs.py",
            "src/blender_mcp/blender_docs_cache.py",
            "src/blender_mcp/blender_docs_retrieval.py",
            "src/blender_mcp/errors.py",
            "src/blender_mcp/geometry_nodes_schema.py",
            "src/blender_mcp/instance_registry.py",
            "src/blender_mcp/node_tree_patch.py",
            "src/blender_mcp/node_tree_schema.py",
            "src/blender_mcp/telemetry.py",
            "src/blender_mcp/telemetry_decorator.py",
        ):
            with self.subTest(path=relative_path):
                self.assertFalse((ROOT / relative_path).exists())

    def test_domain_packages_exist(self):
        for relative_path in (
            "blender_extension/bridge",
            "blender_extension/nodes",
            "blender_extension/providers",
            "src/blender_mcp/transport",
            "src/blender_mcp/tools",
            "src/blender_mcp/protocol",
            "src/blender_mcp/documentation",
            "src/blender_mcp/observability",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue((ROOT / relative_path).is_dir())

    def test_production_packages_have_acyclic_relative_imports(self):
        for relative_root, package_name in (
            ("blender_extension", "blender_extension"),
            ("src/blender_mcp", "blender_mcp"),
        ):
            with self.subTest(package=package_name):
                graph = relative_import_graph(ROOT / relative_root, package_name)
                self.assertEqual(import_cycles(graph), [])

    def test_compatibility_facades_are_small_and_intentional(self):
        for relative_path in (
            "blender_extension/nodes/export.py",
            "blender_extension/nodes/patch.py",
            "src/blender_mcp/documentation/retrieval.py",
            "src/blender_mcp/server.py",
            "src/blender_mcp/tools/nodes.py",
        ):
            with self.subTest(path=relative_path):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("Compatibility", source.splitlines()[0])
                self.assertLessEqual(len(source.splitlines()), 200)

    def test_production_python_modules_have_a_bounded_size(self):
        modules = [
            *ROOT.joinpath("blender_extension").rglob("*.py"),
            *ROOT.joinpath("src", "blender_mcp").rglob("*.py"),
        ]
        oversized = {
            path.relative_to(ROOT).as_posix(): len(
                path.read_text(encoding="utf-8").splitlines()
            )
            for path in modules
            if len(path.read_text(encoding="utf-8").splitlines())
            > MAX_PRODUCTION_MODULE_LINES
        }
        self.assertEqual(oversized, {})

    def test_oversized_production_files_are_explicit_exceptions(self):
        production_files = [ROOT / "bootstrap.ps1", ROOT / "install.ps1"]
        for relative_root in ("blender_extension", "packaging", "scripts", "src"):
            production_files.extend(
                path
                for path in (ROOT / relative_root).rglob("*")
                if path.is_file() and path.suffix.casefold() in {".cmd", ".ps1", ".py"}
            )
        oversized = {
            path.relative_to(ROOT).as_posix()
            for path in production_files
            if len(path.read_text(encoding="utf-8").splitlines())
            > MAX_PRODUCTION_MODULE_LINES
        }
        # install.ps1 remains the stable public entry point; bounded production
        # modules under scripts/installer contain the implementation.
        self.assertEqual(oversized, set())

    def test_windows_installer_has_bounded_modules(self):
        module_root = ROOT / "scripts" / "installer"
        expected = {
            "common.ps1",
            "release.ps1",
            "skills.ps1",
            "discovery.ps1",
            "targets.ps1",
            "clients.ps1",
            "codex-config.ps1",
            "install-main.ps1",
        }
        self.assertEqual({path.name for path in module_root.glob("*.ps1")}, expected)
        self.assertLessEqual(
            len((ROOT / "install.ps1").read_text(encoding="utf-8-sig").splitlines()),
            300,
        )


if __name__ == "__main__":
    unittest.main()
