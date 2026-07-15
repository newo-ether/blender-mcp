from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

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

    def test_manifest_and_python_package_versions_match(self):
        with extension.MANIFEST_SOURCE.open("rb") as handle:
            manifest_version = tomllib.load(handle)["version"]
        with extension.PROJECT_SOURCE.open("rb") as handle:
            project_version = tomllib.load(handle)["project"]["version"]
        self.assertEqual(manifest_version, project_version)


if __name__ == "__main__":
    unittest.main()
