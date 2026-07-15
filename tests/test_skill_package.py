from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_skill_package as package
import verify_release_assets as release


class SkillPackageTests(unittest.TestCase):
    def test_archive_is_reproducible_and_source_equivalent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.zip"
            second = Path(temp_dir) / "second.zip"
            package.build_archive(first)
            package.build_archive(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            package.verify_archive(first)

            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
            self.assertIn("blender-mcp/SKILL.md", names)
            self.assertIn("blender-mcp/agents/openai.yaml", names)
            self.assertIn(
                "blender-mcp/references/node-workflows.md",
                names,
            )

    def test_verifier_rejects_content_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "drift.zip"
            with zipfile.ZipFile(
                archive_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                info = zipfile.ZipInfo("blender-mcp/SKILL.md", package.FIXED_TIMESTAMP)
                archive.writestr(info, b"not the canonical skill")
            with self.assertRaises(RuntimeError):
                package.verify_archive(archive_path)

    def test_release_verifier_requires_source_equivalent_skill_asset(self):
        def write_archive(path: Path, members: list[str]) -> None:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in members:
                    archive.writestr(name, b"fixture")

        with tempfile.TemporaryDirectory() as temp_dir:
            dist = Path(temp_dir)
            version = package.project_version()
            extension = dist / f"blender_mcp-{version}.zip"
            wheel = dist / f"blender_mcp-{version}-py3-none-any.whl"
            mcpb = dist / f"blender_mcp-{version}.mcpb"
            skill = dist / f"blender-mcp-skill-{version}.zip"
            write_archive(
                extension,
                [
                    "__init__.py",
                    "blender_manifest.toml",
                    "schemas/node-tree-v1.json",
                    "schemas/node-tree-patch-v1.json",
                ],
            )
            write_archive(
                wheel,
                [
                    "blender_mcp/server.py",
                    "blender_mcp/node_tree_patch.py",
                    "blender_mcp/schemas/node-tree-v1.json",
                    "blender_mcp/schemas/node-tree-patch-v1.json",
                ],
            )
            write_archive(
                mcpb,
                [
                    "manifest.json",
                    "server/run.cmd",
                    "server/python/blender_mcp/server.py",
                    "server/schemas/node-tree-v1.json",
                ],
            )
            package.build_archive(skill)
            assets = [extension, wheel, mcpb, skill]
            checksum_lines = [
                f"{release.sha256(path)}  {path.name}" for path in assets
            ]
            (dist / "SHA256SUMS.txt").write_text(
                "\n".join(checksum_lines) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(release.verify(dist, version), assets)


if __name__ == "__main__":
    unittest.main()
