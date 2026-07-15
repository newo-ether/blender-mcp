from __future__ import annotations

import bpy

from .nodes.constants import (
    BLENDER_RUNTIME_AUTOMATION_CONTEXT_SCHEMA,
    BLENDER_VERSION_CONTEXT_SCHEMA,
)


def _iter_action_fcurves(action, owner=None):
    """Yield F-Curves from legacy and Blender 5.1+ layered Actions."""
    if action is None:
        return
    layered = bool(getattr(action, "is_action_layered", False))
    legacy_curves = getattr(action, "fcurves", None)
    if legacy_curves is not None and not layered:
        yield from legacy_curves
        return

    slot_handle = None
    if owner is not None:
        animation_data = getattr(owner, "animation_data", None)
        action_slot = getattr(animation_data, "action_slot", None)
        slot_handle = getattr(action_slot, "handle", None)

    seen = set()
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for channelbag in getattr(strip, "channelbags", ()):
                if (
                    slot_handle is not None
                    and getattr(channelbag, "slot_handle", None) != slot_handle
                ):
                    continue
                for fcurve in getattr(channelbag, "fcurves", ()):
                    pointer = fcurve.as_pointer()
                    if pointer in seen:
                        continue
                    seen.add(pointer)
                    yield fcurve

def _runtime_render_automation_context():
    """Probe render identifiers on a disposable Scene, never the live Scene."""
    probe = bpy.data.scenes.new(".BlenderMCP_RuntimeAutomation")
    candidates = (
        "BLENDER_EEVEE",
        "BLENDER_EEVEE_NEXT",
        "CYCLES",
        "BLENDER_WORKBENCH",
    )
    engines = []
    output_formats = {}
    errors = []
    try:
        for identifier in candidates:
            try:
                probe.render.engine = identifier
            except (TypeError, ValueError, RuntimeError) as exc:
                errors.append({
                    "engine": identifier,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            engines.append(identifier)
            image_settings = probe.render.image_settings
            declared = []
            try:
                declared = [
                    item.identifier
                    for item in image_settings.bl_rna.properties["file_format"].enum_items
                ]
            except (AttributeError, KeyError, TypeError, RuntimeError):
                pass
            original_format = image_settings.file_format
            ffmpeg_supported = False
            try:
                image_settings.file_format = "FFMPEG"
                ffmpeg_supported = image_settings.file_format == "FFMPEG"
            except (TypeError, ValueError, RuntimeError):
                ffmpeg_supported = False
            finally:
                try:
                    image_settings.file_format = original_format
                except (TypeError, ValueError, RuntimeError):
                    pass
            output_formats[identifier] = {
                "declared": declared,
                "ffmpeg_supported": ffmpeg_supported,
                "has_ffmpeg_settings": hasattr(probe.render, "ffmpeg"),
            }
    finally:
        bpy.data.scenes.remove(probe, do_unlink=True)

    preferred_eevee = next(
        (item for item in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT") if item in engines),
        None,
    )
    return {
        "available_engines": engines,
        "preferred": {
            "eevee": preferred_eevee,
            "cycles": "CYCLES" if "CYCLES" in engines else None,
            "workbench": "BLENDER_WORKBENCH" if "BLENDER_WORKBENCH" in engines else None,
        },
        "output_formats_by_engine": output_formats,
        "probe_errors": errors,
    }

def _runtime_action_automation_context():
    action = bpy.data.actions.new(".BlenderMCP Runtime Action Probe")
    keyframe_strip = getattr(bpy.types, "ActionKeyframeStrip", None)
    try:
        layered = hasattr(action, "layers")
        legacy = hasattr(action, "fcurves")
        has_slots = hasattr(action, "slots")
        strip_properties = (
            getattr(getattr(keyframe_strip, "bl_rna", None), "properties", None)
            if keyframe_strip is not None else None
        )
        has_channelbags = bool(
            strip_properties is not None
            and strip_properties.get("channelbags") is not None
        )
        return {
            "model": "layered" if layered and not legacy else "legacy_or_hybrid",
            "has_legacy_fcurves": legacy,
            "has_layers": layered,
            "has_slots": has_slots,
            "keyframe_strip_has_channelbags": has_channelbags,
            "fcurve_access": (
                "Action.layers[].strips[].channelbags[].fcurves"
                if layered and not legacy
                else "Action.fcurves or the layered compatibility iterator"
            ),
        }
    finally:
        bpy.data.actions.remove(action, do_unlink=True)

def _runtime_automation_context():
    from .nodes.targets import _node_scene_tree
    scene = bpy.context.scene
    scene_tree, scene_adapter = _node_scene_tree(scene)
    compositor_group = hasattr(scene, "compositing_node_group")
    return {
        "schema": BLENDER_RUNTIME_AUTOMATION_CONTEXT_SCHEMA,
        "blender_version": list(bpy.app.version[:3]),
        "blender_version_string": bpy.app.version_string,
        "build_hash": _blender_app_text("build_hash"),
        "render": _runtime_render_automation_context(),
        "animation": _runtime_action_automation_context(),
        "compositor": {
            "adapter": scene_adapter,
            "tree_exists": scene_tree is not None,
            "requires_explicit_group": compositor_group and scene_tree is None,
            "scene_property": (
                "compositing_node_group" if compositor_group else "node_tree"
            ),
        },
        "geometry_nodes": {
            "hidden_object_info_instance_risk": True,
            "safe_source_strategies": [
                "keep the source render-visible outside the camera",
                "disable Object Info As Instance and instance the returned geometry",
                "realize or author the prototype inside the node tree",
            ],
        },
        "execution_order": [
            "resolve the render engine before destructive scene edits",
            "configure output formats after the final engine is active",
            "create a Scene compositor group explicitly when required",
            "verify evaluated instances and a viewport screenshot before rendering",
        ],
    }

def _blender_app_text(attribute):
    """Read bpy.app build strings consistently across Blender releases."""
    value = getattr(bpy.app, attribute, None)
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = str(value).strip()
    return value or None

def _blender_version_context():
    """Return exact connected-build metadata without reading scene data."""
    version = [int(part) for part in bpy.app.version[:3]]
    version_string = str(bpy.app.version_string)
    version_cycle = str(getattr(bpy.app, "version_cycle", "unknown")).lower()
    commit_timestamp = getattr(bpy.app, "build_commit_timestamp", None)
    if not isinstance(commit_timestamp, int):
        commit_timestamp = None
    return {
        "schema": BLENDER_VERSION_CONTEXT_SCHEMA,
        "version": version,
        "version_string": version_string,
        "version_cycle": version_cycle,
        "is_prerelease": version_cycle not in {"release", "stable", "final"},
        "is_lts": "LTS" in version_string.upper(),
        "build": {
            "branch": _blender_app_text("build_branch"),
            "hash": _blender_app_text("build_hash"),
            "date": _blender_app_text("build_commit_date") or _blender_app_text("build_date"),
            "time": _blender_app_text("build_commit_time") or _blender_app_text("build_time"),
            "platform": _blender_app_text("build_platform"),
            "type": _blender_app_text("build_type"),
            "commit_timestamp": commit_timestamp,
        },
    }
