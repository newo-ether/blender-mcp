"""Verify Blender MCP release filenames, contents, and SHA-256 checksums."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HASH_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_files(path: Path) -> set[str]:
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"Not a ZIP-compatible archive: {path.name}")
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt is not None:
            raise RuntimeError(f"CRC failure in {path.name}: {corrupt}")
        names = {
            name.replace("\\", "/")
            for name in archive.namelist()
            if name and not name.endswith("/")
        }
    for name in names:
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            raise RuntimeError(f"Unsafe archive member in {path.name}: {name}")
    return names


def require_member(names: set[str], expected: str, archive: Path) -> None:
    if expected not in names:
        raise RuntimeError(f"{archive.name} is missing {expected}")


def require_suffix(names: set[str], expected: str, archive: Path) -> None:
    root_name = expected.lstrip("/")
    if not any(name == root_name or name.endswith(expected) for name in names):
        raise RuntimeError(f"{archive.name} is missing *{expected}")


def verify(dist: Path, version: str) -> list[Path]:
    assets = [
        dist / f"blender_mcp-{version}.zip",
        dist / f"blender_mcp-{version}-py3-none-any.whl",
        dist / f"blender_mcp-{version}.mcpb",
        dist / f"blender-mcp-skill-{version}.zip",
    ]
    checksum_path = dist / "SHA256SUMS.txt"
    for path in [*assets, checksum_path]:
        if not path.is_file():
            raise RuntimeError(f"Expected release asset is missing: {path}")

    extension_names = archive_files(assets[0])
    require_member(extension_names, "__init__.py", assets[0])
    require_member(extension_names, "blender_manifest.toml", assets[0])
    require_member(extension_names, "schemas/node-tree-v1.json", assets[0])
    require_member(extension_names, "schemas/node-tree-patch-v1.json", assets[0])

    wheel_names = archive_files(assets[1])
    require_member(wheel_names, "blender_mcp/server.py", assets[1])
    require_member(wheel_names, "blender_mcp/node_tree_patch.py", assets[1])
    require_suffix(
        wheel_names, "/blender_mcp/schemas/node-tree-v1.json", assets[1]
    )
    require_suffix(
        wheel_names, "/blender_mcp/schemas/node-tree-patch-v1.json", assets[1]
    )

    mcpb_names = archive_files(assets[2])
    require_suffix(mcpb_names, "/manifest.json", assets[2])
    require_suffix(mcpb_names, "/server/run.cmd", assets[2])
    require_suffix(mcpb_names, "/server/python/blender_mcp/server.py", assets[2])
    require_suffix(mcpb_names, "/server/schemas/node-tree-v1.json", assets[2])

    skill_root = ROOT / "skills" / "blender-mcp"
    expected_skill = {
        f"blender-mcp/{path.relative_to(skill_root).as_posix()}": path.read_bytes()
        for path in skill_root.rglob("*")
        if path.is_file()
    }
    skill_names = archive_files(assets[3])
    if skill_names != set(expected_skill):
        raise RuntimeError(
            "Skill archive members differ from canonical source: "
            f"expected {sorted(expected_skill)}, got {sorted(skill_names)}"
        )
    with zipfile.ZipFile(assets[3]) as skill_archive:
        for name, expected_content in expected_skill.items():
            if skill_archive.read(name) != expected_content:
                raise RuntimeError(
                    f"{assets[3].name} content differs from source: {name}"
                )

    checksum_entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = HASH_LINE.fullmatch(raw_line)
        if match is None:
            raise RuntimeError(
                f"Malformed SHA256SUMS.txt line {line_number}: {raw_line!r}"
            )
        checksum_entries[match.group(2)] = match.group(1)
    expected_names = {path.name for path in assets}
    if set(checksum_entries) != expected_names:
        raise RuntimeError(
            "Checksum filenames differ from release assets: "
            f"expected {sorted(expected_names)}, got {sorted(checksum_entries)}"
        )
    for asset in assets:
        actual = sha256(asset)
        if actual != checksum_entries[asset.name]:
            raise RuntimeError(f"Checksum mismatch: {asset.name}")
    return assets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=project_version())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assets = verify(args.dist.resolve(), args.version)
    for asset in assets:
        print(f"OK {asset.name} ({asset.stat().st_size:,} bytes)")
    print("OK SHA256SUMS.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
