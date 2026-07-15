"""Provision pinned, repository-local portable Blender test runtimes."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("blender_test_runtimes.json")
DEFAULT_RUNTIME_ROOT = ROOT / ".test-runtime"
OFFICIAL_DOWNLOAD_ROOT = "https://download.blender.org/release"
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60
ALLOWED_DOWNLOAD_HOST = "download.blender.org"


class BlenderTestRuntimeError(RuntimeError):
    """A portable Blender runtime could not be safely prepared."""


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "blender-mcp-test-runtimes/1":
        raise BlenderTestRuntimeError(f"Unsupported runtime manifest: {path}")
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise BlenderTestRuntimeError("Runtime manifest has no versions")
    return manifest


def current_platform_id() -> str:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    system_names = {"windows": "windows", "linux": "linux", "darwin": "darwin"}
    machine_names = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    try:
        return f"{system_names[system]}-{machine_names[machine]}"
    except KeyError as error:
        raise BlenderTestRuntimeError(
            f"No portable Blender test runtime for {system}/{machine}; "
            "pass an explicit VERSION=PATH override"
        ) from error


def resolve_artifact(
    version_alias: str,
    *,
    manifest: dict[str, Any] | None = None,
    platform_id: str | None = None,
) -> dict[str, str]:
    manifest = manifest or load_manifest()
    version = manifest["versions"].get(version_alias)
    if not isinstance(version, dict):
        raise BlenderTestRuntimeError(f"Unknown Blender test version: {version_alias}")
    selected_platform = platform_id or current_platform_id()
    artifact = version.get("artifacts", {}).get(selected_platform)
    if not isinstance(artifact, dict):
        raise BlenderTestRuntimeError(
            f"Blender {version_alias} has no pinned artifact for {selected_platform}; "
            "pass an explicit VERSION=PATH override"
        )
    filename = str(artifact["filename"])
    release_directory = f"Blender{version_alias}"
    return {
        "alias": version_alias,
        "version": str(version["version"]),
        "platform": selected_platform,
        "filename": filename,
        "sha256": str(artifact["sha256"]).casefold(),
        "executable": str(artifact["executable"]),
        "url": f"{OFFICIAL_DOWNLOAD_ROOT}/{release_directory}/{filename}",
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_official_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALLOWED_DOWNLOAD_HOST
        or parsed.port is not None
        or not parsed.path.startswith("/release/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BlenderTestRuntimeError(f"Refusing non-official Blender URL: {url}")


def download_verified(artifact: dict[str, str], downloads: Path) -> Path:
    downloads.mkdir(parents=True, exist_ok=True)
    destination = downloads / artifact["filename"]
    expected = artifact["sha256"]
    if destination.is_file() and sha256_file(destination) == expected:
        print(f"Using verified download cache: {destination}")
        return destination
    destination.unlink(missing_ok=True)

    url = artifact["url"]
    _validate_official_url(url)
    temporary = destination.with_name(destination.name + ".download")
    temporary.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "blender-mcp-test-runtime/1"})
    print(f"Downloading Blender {artifact['version']} from {url}")
    try:
        with urlopen(
            request, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response, temporary.open("wb") as output:
            if getattr(response, "status", 200) != 200:
                raise BlenderTestRuntimeError(f"Download returned HTTP {response.status}")
            while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                output.write(chunk)
        actual = sha256_file(temporary)
        if actual != expected:
            raise BlenderTestRuntimeError(
                f"SHA-256 mismatch for {artifact['filename']}: {actual} != {expected}"
            )
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _safe_archive_destination(root: Path, member_name: str) -> Path:
    member = Path(member_name.replace("\\", "/"))
    if member.is_absolute() or ".." in member.parts:
        raise BlenderTestRuntimeError(f"Unsafe archive member: {member_name}")
    destination = (root / member).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as error:
        raise BlenderTestRuntimeError(f"Archive member escapes target: {member_name}") from error
    return destination


def _extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = _safe_archive_destination(destination, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, mode="r:xz") as package:
        for member in package.getmembers():
            _safe_archive_destination(destination, member.name)
            if member.isdev() or member.isfifo():
                raise BlenderTestRuntimeError(f"Unsupported archive member: {member.name}")
            if member.issym() or member.islnk():
                link_parent = Path(member.name).parent
                _safe_archive_destination(destination, str(link_parent / member.linkname))
        package.extractall(destination)


def _extract_dmg(archive: Path, destination: Path) -> None:
    if sys.platform != "darwin":
        raise BlenderTestRuntimeError("DMG extraction is only supported on macOS")
    attach = subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(archive)],
        check=True,
        capture_output=True,
    )
    details = plistlib.loads(attach.stdout)
    mount_points = [
        entity.get("mount-point")
        for entity in details.get("system-entities", [])
        if entity.get("mount-point")
    ]
    if len(mount_points) != 1:
        raise BlenderTestRuntimeError("Unable to identify the Blender DMG mount point")
    mount_point = Path(mount_points[0])
    try:
        source = mount_point / "Blender.app"
        if not source.is_dir():
            raise BlenderTestRuntimeError("Blender.app is missing from the official DMG")
        shutil.copytree(source, destination / "Blender.app", symlinks=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount_point)], check=True)


def extract_archive(archive: Path, destination: Path) -> None:
    if archive.name.endswith(".zip"):
        _extract_zip(archive, destination)
    elif archive.name.endswith(".tar.xz"):
        _extract_tar(archive, destination)
    elif archive.name.endswith(".dmg"):
        _extract_dmg(archive, destination)
    else:
        raise BlenderTestRuntimeError(f"Unsupported Blender archive: {archive.name}")


def _completed_executable(install_root: Path, artifact: dict[str, str]) -> Path | None:
    marker = install_root / ".complete.json"
    executable = install_root / artifact["executable"]
    if not marker.is_file() or not executable.is_file():
        return None
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    expected = {
        "schema": "blender-mcp-test-runtime/1",
        "version": artifact["version"],
        "platform": artifact["platform"],
        "filename": artifact["filename"],
        "sha256": artifact["sha256"],
    }
    return executable if metadata == expected else None


def ensure_runtime(
    version_alias: str,
    *,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    manifest: dict[str, Any] | None = None,
    platform_id: str | None = None,
    offline: bool = False,
) -> Path:
    artifact = resolve_artifact(
        version_alias, manifest=manifest, platform_id=platform_id
    )
    install_root = runtime_root / "blender" / f"{artifact['version']}-{artifact['platform']}"
    completed = _completed_executable(install_root, artifact)
    if completed is not None:
        print(f"Using portable Blender {artifact['version']}: {completed}")
        return completed

    archive = runtime_root / "downloads" / artifact["filename"]
    if offline:
        if not archive.is_file() or sha256_file(archive) != artifact["sha256"]:
            raise BlenderTestRuntimeError(
                f"Offline mode requires a verified archive at {archive}"
            )
    else:
        archive = download_verified(artifact, runtime_root / "downloads")

    staging_root = runtime_root / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="blender-", dir=staging_root))
    try:
        extract_archive(archive, staging)
        executable = staging / artifact["executable"]
        if not executable.is_file():
            raise BlenderTestRuntimeError(
                f"Archive did not contain {artifact['executable']}"
            )
        if os.name != "nt":
            executable.chmod(executable.stat().st_mode | 0o111)
        marker = {
            "schema": "blender-mcp-test-runtime/1",
            "version": artifact["version"],
            "platform": artifact["platform"],
            "filename": artifact["filename"],
            "sha256": artifact["sha256"],
        }
        (staging / ".complete.json").write_text(
            json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8"
        )
        if install_root.exists():
            shutil.rmtree(install_root)
        install_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, install_root)
        return install_root / artifact["executable"]
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
