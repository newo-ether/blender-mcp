"""Build and verify the portable Blender MCP Agent Skill archive."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "blender-mcp"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def skill_files(skill_root: Path = SKILL_ROOT) -> list[Path]:
    required = skill_root / "SKILL.md"
    if not required.is_file():
        raise RuntimeError(f"Canonical Skill is missing: {required}")
    files = sorted(
        (path for path in skill_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(skill_root).as_posix(),
    )
    if any("__pycache__" in path.parts or path.suffix == ".pyc" for path in files):
        raise RuntimeError("Generated Python files must not enter the Skill archive")
    return files


def archive_members(skill_root: Path = SKILL_ROOT) -> dict[str, bytes]:
    return {
        f"{skill_root.name}/{path.relative_to(skill_root).as_posix()}": path.read_bytes()
        for path in skill_files(skill_root)
    }


def verify_archive(archive_path: Path, skill_root: Path = SKILL_ROOT) -> None:
    expected = archive_members(skill_root)
    if not zipfile.is_zipfile(archive_path):
        raise RuntimeError(f"Not a ZIP archive: {archive_path}")
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f"CRC failure in {archive_path.name}")
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = {info.filename for info in infos}
        if names != set(expected):
            raise RuntimeError(
                "Skill archive members differ from canonical source: "
                f"expected {sorted(expected)}, got {sorted(names)}"
            )
        for info in infos:
            pure = PurePosixPath(info.filename)
            if pure.is_absolute() or ".." in pure.parts:
                raise RuntimeError(f"Unsafe archive member: {info.filename}")
            if info.date_time != FIXED_TIMESTAMP:
                raise RuntimeError(f"Non-reproducible timestamp: {info.filename}")
            if archive.read(info) != expected[info.filename]:
                raise RuntimeError(f"Skill content differs from source: {info.filename}")


def build_archive(
    output_path: Path,
    skill_root: Path = SKILL_ROOT,
) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    members = archive_members(skill_root)
    with zipfile.ZipFile(
        output_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name, content in sorted(members.items()):
            info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)
    verify_archive(output_path, skill_root)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=project_version())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output_dir / f"blender-mcp-skill-{args.version}.zip"
    built = build_archive(output)
    print(f"OK {built} ({built.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
