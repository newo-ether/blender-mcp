from __future__ import annotations

import ast
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "blender_test_runtime.py"
SPEC = importlib.util.spec_from_file_location("blender_test_runtime_test", MODULE_PATH)
runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runtime)


class BlenderTestRuntimeTests(unittest.TestCase):
    def test_default_runtime_is_repository_local_and_ignored(self):
        self.assertEqual(runtime.DEFAULT_RUNTIME_ROOT, ROOT / ".test-runtime")
        ignore_lines = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        }
        self.assertIn(".test-runtime/", ignore_lines)

    def test_manifest_pins_official_artifacts_and_hashes(self):
        manifest = runtime.load_manifest()
        self.assertEqual(set(manifest["versions"]), {"4.2", "5.1", "5.2"})
        for alias, version in manifest["versions"].items():
            self.assertTrue(
                version["version"] == alias
                or version["version"].startswith(alias + ".")
            )
            for platform_id, raw in version["artifacts"].items():
                with self.subTest(version=alias, platform=platform_id):
                    artifact = runtime.resolve_artifact(
                        alias, manifest=manifest, platform_id=platform_id
                    )
                    self.assertEqual(len(raw["sha256"]), 64)
                    int(raw["sha256"], 16)
                    runtime._validate_official_url(artifact["url"])

    def test_non_official_download_urls_are_rejected(self):
        for url in (
            "http://download.blender.org/release/Blender5.2/file.zip",
            "https://example.com/release/Blender5.2/file.zip",
            "https://user@download.blender.org/release/file.zip",
            "https://download.blender.org:444/release/file.zip",
            "https://download.blender.org/release/file.zip?changed=1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(runtime.BlenderTestRuntimeError):
                    runtime._validate_official_url(url)

    def test_zip_extraction_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../outside.txt", "unsafe")
            with self.assertRaises(runtime.BlenderTestRuntimeError):
                runtime.extract_archive(archive, root / "output")
            self.assertFalse((root / "outside.txt").exists())

    def test_completed_runtime_requires_exact_marker(self):
        artifact = runtime.resolve_artifact("5.2", platform_id="windows-x86_64")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / artifact["executable"]
            executable.parent.mkdir(parents=True)
            executable.touch()
            self.assertIsNone(runtime._completed_executable(root, artifact))
            marker = {
                "schema": "blender-mcp-test-runtime/1",
                "version": artifact["version"],
                "platform": artifact["platform"],
                "filename": artifact["filename"],
                "sha256": artifact["sha256"],
            }
            (root / ".complete.json").write_text(json.dumps(marker), encoding="utf-8")
            self.assertEqual(runtime._completed_executable(root, artifact), executable)

    def test_runner_has_no_implicit_machine_discovery(self):
        source = (ROOT / "scripts" / "run_blender_acceptance.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "Program Files",
            "APPDATA",
            'which("blender")',
            "." + "codex",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_runner_declares_the_complete_core_matrix(self):
        source = (ROOT / "scripts" / "run_blender_acceptance.py").read_text(
            encoding="utf-8"
        )
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"ALL_SUITES", "CORE_CASES"}
        }
        self.assertIn("core", assignments["ALL_SUITES"])
        self.assertEqual(
            {script for _name, script in assignments["CORE_CASES"]},
            {
                "blender_compositor_initialization.py",
                "blender_compositor_nodes_transactions.py",
                "blender_geometry_nodes_linked.py",
                "blender_geometry_nodes_readonly.py",
                "blender_geometry_nodes_scale.py",
                "blender_instance_lifecycle.py",
                "blender_node_tree_corner_cases.py",
                "blender_node_tree_model_efficiency.py",
                "blender_node_tree_validation.py",
                "blender_node_trees_readonly.py",
                "blender_shader_compositor_capabilities.py",
                "blender_shader_compositor_dynamic.py",
                "blender_shader_compositor_linked.py",
                "blender_shader_compositor_scale.py",
                "blender_shader_compositor_transactions.py",
                "blender_shader_nodes_transactions.py",
                "blender_version_context.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
