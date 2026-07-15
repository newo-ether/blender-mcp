"""Live Blender acceptance for documentation version/build metadata.

Run with:
    blender --background --factory-startup --python tests/blender_version_context.py -- \
        --addon blender_extension/__init__.py --resolver src/blender_mcp/documentation/context.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon", required=True)
    parser.add_argument("--resolver", required=True)
    return parser.parse_args(arguments)


def main() -> None:
    args = parse_args()
    addon_path = Path(args.addon).resolve()
    spec = importlib.util.spec_from_file_location(
        "blender_mcp_version_context_acceptance",
        addon_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load add-on module from {addon_path}")
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    try:
        spec.loader.exec_module(addon)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise

    resolver_path = Path(args.resolver).resolve()
    resolver_spec = importlib.util.spec_from_file_location(
        "blender_mcp_documentation_resolver_acceptance",
        resolver_path,
    )
    if resolver_spec is None or resolver_spec.loader is None:
        raise RuntimeError(f"Unable to load documentation resolver from {resolver_path}")
    resolver = importlib.util.module_from_spec(resolver_spec)
    resolver_spec.loader.exec_module(resolver)

    context = addon._blender_version_context()
    expected_version = [int(part) for part in bpy.app.version[:3]]
    assert context["schema"] == "blender-version-context/1"
    assert context["version"] == expected_version
    assert context["version_string"] == bpy.app.version_string
    assert isinstance(context["version_cycle"], str)
    assert isinstance(context["is_prerelease"], bool)
    assert isinstance(context["is_lts"], bool)
    assert list(context["build"]) == [
        "branch",
        "hash",
        "date",
        "time",
        "platform",
        "type",
        "commit_timestamp",
    ]
    json.dumps(context, ensure_ascii=False)

    documentation = resolver.resolve_documentation_context(
        version="auto",
        language="zh_CN",
        detected_blender=context,
    )
    expected_minor = f"{expected_version[0]}.{expected_version[1]}"
    assert documentation["resolved"]["version"] == expected_minor
    assert documentation["detected_blender"]["build"]["hash"] == context["build"]["hash"]
    manual, api, release_notes = documentation["sources"]
    if context["is_prerelease"]:
        assert manual["channel"] == "dev"
        assert api["channel"] == "dev"
    else:
        assert manual["channel"] == expected_minor
        assert api["channel"] == expected_minor
    assert release_notes["channel"] == expected_minor
    assert manual["language"] == "zh-hans"
    assert api["language"] == "en"
    assert release_notes["language"] == "en"

    print("BLENDER_BUILD_ACCEPTANCE=" + json.dumps(context, ensure_ascii=False, sort_keys=True))
    print("BLENDER_DOCS_ACCEPTANCE=" + json.dumps(documentation, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
