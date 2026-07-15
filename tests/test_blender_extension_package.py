from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_blender_extension as extension  # noqa: E402


class BlenderExtensionPackageTests(unittest.TestCase):
    def _version_tree(self):
        return ast.parse(extension.VERSION_SOURCE.read_text(encoding="utf-8"))

    def _addon_version(self):
        for statement in self._version_tree().body:
            if not isinstance(statement, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name) and target.id == "BLENDER_MCP_ADDON_VERSION"
                for target in statement.targets
            ):
                return ast.literal_eval(statement.value)
        self.fail("BLENDER_MCP_ADDON_VERSION is missing from blender_extension/version.py")

    def test_staged_sources_match_declared_archive_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            extension.stage_sources(staging)
            actual = {
                path.relative_to(staging).as_posix()
                for path in staging.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, extension.expected_archive_members())

    def test_manifest_build_paths_match_archive_contract(self):
        with extension.MANIFEST_SOURCE.open("rb") as handle:
            manifest_paths = set(tomllib.load(handle)["build"]["paths"])
        self.assertEqual(
            manifest_paths,
            extension.expected_archive_members() - {"blender_manifest.toml"},
        )

    def test_manifest_and_python_package_versions_match(self):
        with extension.MANIFEST_SOURCE.open("rb") as handle:
            manifest_version = tomllib.load(handle)["version"]
        with extension.PROJECT_SOURCE.open("rb") as handle:
            project_version = tomllib.load(handle)["project"]["version"]
        self.assertEqual(manifest_version, project_version)
        self.assertEqual(
            ".".join(str(value) for value in self._addon_version()),
            project_version,
        )

    def test_extension_runtime_does_not_depend_on_legacy_bl_info(self):
        references = []
        for path in extension.EXTENSION_SOURCE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            references.extend(
                (path.relative_to(extension.EXTENSION_SOURCE).as_posix(), node.lineno)
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id == "bl_info"
            )
        self.assertEqual(references, [])

    def test_extension_runtime_never_saves_global_preferences(self):
        forbidden = ("save_userpref", "read_factory_settings")
        references = {
            name: [
                path.relative_to(extension.EXTENSION_SOURCE).as_posix()
                for path in extension.EXTENSION_SOURCE.rglob("*.py")
                if name in path.read_text(encoding="utf-8")
            ]
            for name in forbidden
        }
        self.assertEqual(references, {name: [] for name in forbidden})

    def test_extension_is_split_into_bounded_domain_modules(self):
        modules = list(extension.EXTENSION_SOURCE.rglob("*.py"))
        self.assertGreaterEqual(len(modules), 12)
        oversized = {
            path.relative_to(extension.EXTENSION_SOURCE).as_posix(): len(
                path.read_text(encoding="utf-8").splitlines()
            )
            for path in modules
            if len(path.read_text(encoding="utf-8").splitlines()) > 1000
        }
        self.assertEqual(oversized, {})


if __name__ == "__main__":
    unittest.main()
