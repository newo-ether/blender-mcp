"""Build and validate the installable Blender MCP Extension archive."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ADDON_SOURCE = ROOT / "addon.py"
MANIFEST_SOURCE = ROOT / "packaging" / "blender_extension" / "blender_manifest.toml"
LICENSE_SOURCE = ROOT / "LICENSE"
PROJECT_SOURCE = ROOT / "pyproject.toml"


def find_blender(explicit_path: str | None) -> Path:
    """Find a Blender executable suitable for the official extension builder."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    if os.environ.get("BLENDER_EXECUTABLE"):
        candidates.append(Path(os.environ["BLENDER_EXECUTABLE"]))

    path_match = shutil.which("blender")
    if path_match:
        candidates.append(Path(path_match))

    if sys.platform == "win32":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        blender_root = program_files / "Blender Foundation"
        if blender_root.is_dir():
            candidates.extend(
                sorted(
                    blender_root.glob("Blender */blender.exe"),
                    reverse=True,
                )
            )

    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved

    raise FileNotFoundError(
        "Blender executable not found. Pass --blender or set BLENDER_EXECUTABLE."
    )


def run_blender(blender: Path, *arguments: str) -> None:
    command = [
        str(blender),
        "--factory-startup",
        "--command",
        "extension",
        *arguments,
    ]
    subprocess.run(command, cwd=ROOT, check=True)


def verify_archive(archive_path: Path) -> None:
    """Reject accidental extra files or nested archive roots."""
    expected = {"__init__.py", "blender_manifest.toml", "LICENSE"}
    with zipfile.ZipFile(archive_path) as archive:
        actual = {name.rstrip("/") for name in archive.namelist() if name.rstrip("/")}
        if actual != expected:
            raise RuntimeError(
                f"Unexpected archive contents: expected {sorted(expected)}, got {sorted(actual)}"
            )
        if archive.testzip() is not None:
            raise RuntimeError("The generated ZIP failed its CRC check")


def build(blender: Path, output_dir: Path) -> Path:
    with MANIFEST_SOURCE.open("rb") as handle:
        manifest = tomllib.load(handle)
    with PROJECT_SOURCE.open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]
    if manifest["version"] != project_version:
        raise RuntimeError(
            "Extension manifest version must match pyproject.toml: "
            f"{manifest['version']} != {project_version}"
        )
    archive_name = f"{manifest['id']}-{manifest['version']}.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = (output_dir / archive_name).resolve()

    with tempfile.TemporaryDirectory(prefix="blender-mcp-extension-") as temp_dir:
        staging = Path(temp_dir)
        shutil.copy2(ADDON_SOURCE, staging / "__init__.py")
        shutil.copy2(MANIFEST_SOURCE, staging / "blender_manifest.toml")
        shutil.copy2(LICENSE_SOURCE, staging / "LICENSE")

        run_blender(
            blender,
            "build",
            "--source-dir",
            str(staging),
            "--output-filepath",
            str(archive_path),
        )

    verify_archive(archive_path)
    run_blender(blender, "validate", str(archive_path))
    return archive_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an installable Blender MCP Extension ZIP"
    )
    parser.add_argument(
        "--blender",
        help="Path to blender executable (otherwise auto-detected)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="Artifact directory (default: dist)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    blender = find_blender(args.blender)
    archive_path = build(blender, args.output_dir.resolve())
    print(f"Built and validated: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
