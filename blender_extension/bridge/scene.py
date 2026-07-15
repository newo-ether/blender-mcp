from __future__ import annotations

import hashlib
import io
import math
import os
import os.path as osp
import time
from contextlib import redirect_stdout, suppress

import bpy
import mathutils

from ..errors import BlenderMCPAddonError
from ..nodes.modifiers import _gn_modifier_input_record
from ..nodes.schema import _gn_blend_data_ids
from ..nodes.serialization import _gn_canonical_json
from ..nodes.targets import _node_id_editable, _node_id_library


class SceneDiagnosticsMixin:
    @staticmethod
    def _get_aabb(obj):
        """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [
            [*min_corner], [*max_corner]
        ]

    def get_object_info(self, name, include_modifiers=False):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
            "modifiers": [],
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box

        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        # Add mesh data if applicable
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        for index, modifier in enumerate(obj.modifiers):
            record = {
                "index": index,
                "name": modifier.name,
                "type": modifier.type,
                "show_viewport": bool(modifier.show_viewport),
                "show_render": bool(modifier.show_render),
                "show_in_editmode": bool(getattr(modifier, "show_in_editmode", False)),
                "show_on_cage": bool(getattr(modifier, "show_on_cage", False)),
            }
            node_group = getattr(modifier, "node_group", None)
            if node_group is not None:
                record["node_group"] = {
                    "name": node_group.name,
                    "library": _node_id_library(node_group),
                    "editable": _node_id_editable(node_group),
                }
                if include_modifiers:
                    inputs = []
                    interface = getattr(node_group, "interface", None)
                    for item in getattr(interface, "items_tree", ()):
                        if getattr(item, "item_type", "") != "SOCKET" or getattr(item, "in_out", "") != "INPUT":
                            continue
                        identifier = getattr(item, "identifier", "")
                        try:
                            state = _gn_modifier_input_record(modifier, identifier)
                        except KeyError:
                            continue
                        inputs.append({
                            "identifier": identifier,
                            "name": item.name,
                            "socket_type": getattr(item, "socket_type", ""),
                            **state,
                        })
                    record["inputs"] = inputs
                    simulation_records = self._simulation_modifier_records(
                        obj.name, modifier.name
                    )
                    simulation_bakes = (
                        simulation_records[0]["bakes"] if simulation_records else []
                    )
                    record["simulation"] = {
                        "zone_count": len(simulation_bakes),
                        "bakes": simulation_bakes,
                    }
            warning = getattr(modifier, "error", "")
            if warning:
                record["warning"] = warning
            obj_info["modifiers"].append(record)

        return obj_info

    def _external_dependency_records(self):
        records = []
        collections = (
            ("LIBRARY", bpy.data.libraries),
            ("IMAGE", bpy.data.images),
            ("MOVIE_CLIP", bpy.data.movieclips),
            ("SOUND", bpy.data.sounds),
            ("FONT", bpy.data.fonts),
            ("CACHE_FILE", bpy.data.cache_files),
            ("VOLUME", bpy.data.volumes),
        )
        for id_type, collection in collections:
            for item in sorted(collection, key=lambda value: value.name):
                if id_type == "IMAGE" and (
                    getattr(item, "packed_file", None)
                    or getattr(item, "packed_files", None)
                    or getattr(item, "source", "") == "GENERATED"
                ):
                    continue
                raw_path = str(getattr(item, "filepath", "") or "")
                if not raw_path:
                    continue
                try:
                    resolved = bpy.path.abspath(raw_path, library=getattr(item, "library", None))
                except (AttributeError, TypeError, ValueError):
                    resolved = bpy.path.abspath(raw_path)
                resolved = osp.normpath(osp.abspath(resolved))
                records.append({
                    "id_type": id_type,
                    "name": item.name,
                    "filepath": raw_path,
                    "resolved_path": resolved,
                    "exists": osp.exists(resolved),
                    "packed": False,
                })
        return records

    def audit_external_dependencies(self, missing_only=True):
        """Report linked libraries and external files without changing paths."""
        records = self._external_dependency_records()
        visible = [item for item in records if not item["exists"]] if missing_only else records
        counts = {}
        for item in records:
            counts[item["id_type"]] = counts.get(item["id_type"], 0) + 1
        return {
            "schema": "blender-external-dependency-audit/1",
            "blend_file": bpy.data.filepath or "Untitled",
            "missing_only": bool(missing_only),
            "dependency_count": len(records),
            "missing_count": sum(not item["exists"] for item in records),
            "counts_by_type": counts,
            "dependencies": visible,
            "mutated": False,
        }

    def plan_external_dependency_relinks(self, search_roots, max_files=10000):
        """Build an explicit, non-mutating relink plan using exact basenames."""
        if not isinstance(search_roots, (list, tuple)) or not search_roots:
            raise ValueError("search_roots must contain at least one directory")
        max_files = int(max_files)
        if not 1 <= max_files <= 100000:
            raise ValueError("max_files must be from 1 to 100000")
        roots = []
        for value in search_roots:
            root = osp.normpath(osp.abspath(bpy.path.abspath(str(value))))
            if not osp.isdir(root):
                raise ValueError(f"Search root is not a directory: {value}")
            roots.append(root)
        candidates = {}
        scanned = 0
        truncated = False
        for root in roots:
            for directory, directory_names, file_names in os.walk(root, followlinks=False):
                directory_names[:] = sorted(name for name in directory_names if not name.startswith("."))
                for file_name in sorted(file_names):
                    candidates.setdefault(file_name.casefold(), []).append(
                        osp.normpath(osp.join(directory, file_name))
                    )
                    scanned += 1
                    if scanned >= max_files:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break
        actions = []
        unresolved = []
        for item in self._external_dependency_records():
            if item["exists"]:
                continue
            matches = sorted(set(candidates.get(osp.basename(item["resolved_path"]).casefold(), [])))
            if len(matches) == 1:
                actions.append({
                    "id_type": item["id_type"],
                    "name": item["name"],
                    "expected_filepath": item["filepath"],
                    "new_filepath": matches[0],
                })
            else:
                unresolved.append({**item, "candidates": matches[:20], "ambiguous": len(matches) > 1})
        plan_core = {"search_roots": roots, "actions": actions}
        revision = "sha256:" + hashlib.sha256(_gn_canonical_json(plan_core).encode("utf-8")).hexdigest()
        return {
            "schema": "blender-external-dependency-relink-plan/1",
            "revision": revision,
            "search_roots": roots,
            "scanned_files": scanned,
            "truncated": truncated,
            "actions": actions,
            "unresolved": unresolved,
            "mutated": False,
        }

    def apply_external_dependency_relinks(self, plan):
        """Apply only an explicit unambiguous relink plan with rollback on error."""
        if not isinstance(plan, dict) or plan.get("schema") != "blender-external-dependency-relink-plan/1":
            raise ValueError("A relink plan from plan_external_dependency_relinks is required")
        core = {"search_roots": plan.get("search_roots", []), "actions": plan.get("actions", [])}
        expected_revision = "sha256:" + hashlib.sha256(_gn_canonical_json(core).encode("utf-8")).hexdigest()
        if plan.get("revision") != expected_revision:
            raise ValueError("Relink plan revision is invalid")
        collections = {
            "LIBRARY": bpy.data.libraries,
            "IMAGE": bpy.data.images,
            "MOVIE_CLIP": bpy.data.movieclips,
            "SOUND": bpy.data.sounds,
            "FONT": bpy.data.fonts,
            "CACHE_FILE": bpy.data.cache_files,
            "VOLUME": bpy.data.volumes,
        }
        changed = []
        try:
            for action in plan.get("actions", []):
                collection = collections.get(action.get("id_type"))
                item = collection.get(action.get("name")) if collection is not None else None
                if item is None:
                    raise ValueError(f"Dependency no longer exists: {action.get('id_type')}/{action.get('name')}")
                if item.filepath != action.get("expected_filepath"):
                    raise ValueError(f"Dependency path changed before apply: {item.name}")
                new_path = osp.normpath(osp.abspath(action.get("new_filepath", "")))
                if not osp.isfile(new_path):
                    raise ValueError(f"Relink candidate no longer exists: {new_path}")
                changed.append((item, item.filepath))
                item.filepath = new_path
            verification = self.audit_external_dependencies(missing_only=True)
            return {
                "schema": "blender-external-dependency-relink-application/1",
                "status": "applied",
                "applied": True,
                "mutated": bool(changed),
                "changed": [
                    {"id_type": item.bl_rna.identifier, "name": item.name, "filepath": item.filepath}
                    for item, _old in changed
                ],
                "missing_after": verification["missing_count"],
            }
        except Exception:
            for item, old_path in reversed(changed):
                with suppress(Exception):
                    item.filepath = old_path
            raise

    def inspect_evaluated_mesh(self, object_name, max_attributes=32, max_attribute_values=4096):
        """Return bounded topology, bounds, edge, and Named Attribute diagnostics."""
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != 'MESH':
            raise ValueError(f"Mesh object not found: {object_name}")
        max_attributes = max(0, min(int(max_attributes), 128))
        max_attribute_values = max(1, min(int(max_attribute_values), 100000))
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            vertex_count = len(mesh.vertices)
            parent = list(range(vertex_count))

            def find(index):
                while parent[index] != index:
                    parent[index] = parent[parent[index]]
                    index = parent[index]
                return index

            def union(left, right):
                left_root, right_root = find(left), find(right)
                if left_root != right_root:
                    parent[right_root] = left_root

            lengths = []
            for edge in mesh.edges:
                left, right = edge.vertices
                union(left, right)
                lengths.append((mesh.vertices[left].co - mesh.vertices[right].co).length)
            lengths.sort()
            non_finite = 0
            world_points = []
            for vertex in mesh.vertices:
                values = tuple(float(value) for value in vertex.co)
                non_finite += sum(not math.isfinite(value) for value in values)
                world_points.append(evaluated.matrix_world @ vertex.co)
            if world_points:
                bounds = [
                    [min(point[index] for point in world_points) for index in range(3)],
                    [max(point[index] for point in world_points) for index in range(3)],
                ]
            else:
                bounds = [None, None]
            components = len({find(index) for index in range(vertex_count)}) if vertex_count else 0
            edge_stats = {
                "count": len(lengths),
                "min": lengths[0] if lengths else None,
                "max": lengths[-1] if lengths else None,
                "mean": (sum(lengths) / len(lengths)) if lengths else None,
                "median": lengths[len(lengths) // 2] if lengths else None,
            }
            attributes = []
            for attribute in sorted(mesh.attributes, key=lambda value: value.name)[:max_attributes]:
                numeric = []
                for value in list(attribute.data)[:max_attribute_values]:
                    candidate = None
                    for field in ("value", "vector", "color"):
                        if hasattr(value, field):
                            candidate = getattr(value, field)
                            break
                    if candidate is None:
                        continue
                    values = candidate if hasattr(candidate, "__iter__") and not isinstance(candidate, str) else (candidate,)
                    for component in values:
                        if isinstance(component, (int, float, bool)):
                            numeric.append(float(component))
                attributes.append({
                    "name": attribute.name,
                    "domain": attribute.domain,
                    "data_type": attribute.data_type,
                    "element_count": len(attribute.data),
                    "sampled_values": min(len(attribute.data), max_attribute_values),
                    "range": {"min": min(numeric), "max": max(numeric)} if numeric else None,
                    "non_finite": sum(not math.isfinite(value) for value in numeric),
                })
            return {
                "schema": "blender-evaluated-mesh-inspection/1",
                "object": obj.name,
                "original": {
                    "vertices": len(obj.data.vertices),
                    "edges": len(obj.data.edges),
                    "polygons": len(obj.data.polygons),
                },
                "evaluated": {
                    "vertices": vertex_count,
                    "edges": len(mesh.edges),
                    "polygons": len(mesh.polygons),
                    "connected_components": components,
                    "world_bounding_box": bounds,
                    "edge_lengths": edge_stats,
                    "position_non_finite": non_finite,
                    "attributes": attributes,
                    "attributes_truncated": len(mesh.attributes) > max_attributes,
                },
                "cleanup": {"temporary_mesh_cleared": True},
            }
        finally:
            evaluated.to_mesh_clear()

    def _simulation_modifier_records(self, object_name="", modifier_name=""):
        records = []
        objects = [bpy.data.objects.get(object_name)] if object_name else sorted(bpy.data.objects, key=lambda item: item.name)
        for obj in objects:
            if obj is None:
                continue
            for modifier in obj.modifiers:
                if modifier.type != 'NODES' or (modifier_name and modifier.name != modifier_name):
                    continue
                bakes = []
                for bake in getattr(modifier, "bakes", ()):
                    node = getattr(bake, "node", None)
                    if node is None or node.bl_idname != "GeometryNodeSimulationOutput":
                        continue
                    directory = str(getattr(bake, "directory", "") or getattr(modifier, "bake_directory", "") or "")
                    resolved_directory = bpy.path.abspath(directory) if directory else ""
                    bakes.append({
                        "bake_id": int(bake.bake_id),
                        "node": node.name,
                        "frame_start": int(bake.frame_start),
                        "frame_end": int(bake.frame_end),
                        "bake_mode": str(bake.bake_mode),
                        "bake_target": str(bake.bake_target),
                        "directory": directory,
                        "resolved_directory": resolved_directory,
                        "directory_exists": bool(resolved_directory and osp.exists(resolved_directory)),
                        "data_block_count": len(getattr(bake, "data_blocks", ())),
                    })
                if bakes:
                    records.append({
                        "object": obj.name,
                        "object_session_uid": int(obj.session_uid),
                        "modifier": modifier.name,
                        "node_group": modifier.node_group.name if modifier.node_group else None,
                        "bake_target": str(getattr(modifier, "bake_target", "")),
                        "bake_directory": str(getattr(modifier, "bake_directory", "")),
                        "bakes": bakes,
                    })
        return records

    def get_simulation_status(self, object_name="", modifier_name=""):
        records = self._simulation_modifier_records(object_name, modifier_name)
        return {
            "schema": "blender-simulation-status/1",
            "blender_version": list(bpy.app.version[:3]),
            "scene_frame": int(bpy.context.scene.frame_current),
            "modifier_count": len(records),
            "simulation_count": sum(len(item["bakes"]) for item in records),
            "modifiers": records,
            "capabilities": {
                "status": True,
                "reset": hasattr(bpy.ops.object, "geometry_node_bake_delete_single"),
                "clear": hasattr(bpy.ops.object, "geometry_node_bake_delete_single"),
                "bake": hasattr(bpy.ops.object, "geometry_node_bake_single"),
                "cancellable_bake": False,
            },
        }

    def _run_simulation_bake_operator(self, operator_name, object_name, modifier_name, bake_id):
        obj = bpy.data.objects.get(object_name)
        modifier = obj.modifiers.get(modifier_name) if obj else None
        if obj is None or modifier is None or modifier.type != 'NODES':
            raise ValueError(f"Geometry Nodes modifier not found: {object_name}/{modifier_name}")
        bake_ids = {int(item.bake_id) for item in getattr(modifier, "bakes", ())}
        if int(bake_id) not in bake_ids:
            raise ValueError(f"Simulation bake id not found: {bake_id}")
        operator = getattr(bpy.ops.object, operator_name, None)
        if operator is None:
            raise BlenderMCPAddonError(
                "simulation_operation_unsupported",
                f"{operator_name} is unavailable in Blender {bpy.app.version_string}",
            )
        with bpy.context.temp_override(
            object=obj,
            active_object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            result = operator(
                session_uid=int(obj.session_uid),
                modifier_name=modifier.name,
                bake_id=int(bake_id),
            )
        if 'FINISHED' not in result:
            raise BlenderMCPAddonError(
                "simulation_operation_failed",
                f"Blender returned {sorted(result)} for {operator_name}",
            )
        return sorted(result)

    def clear_simulation_cache(self, object_name, modifier_name, bake_id=None):
        status_before = self.get_simulation_status(object_name, modifier_name)
        targets = [
            bake["bake_id"]
            for record in status_before["modifiers"]
            for bake in record["bakes"]
            if bake_id is None or bake["bake_id"] == int(bake_id)
        ]
        if not targets:
            raise ValueError("No matching simulation cache was found")
        for target in targets:
            self._run_simulation_bake_operator(
                "geometry_node_bake_delete_single", object_name, modifier_name, target
            )
        return {
            "schema": "blender-simulation-operation/1",
            "operation": "clear",
            "status": "completed",
            "cleared_bake_ids": targets,
            "verification": self.get_simulation_status(object_name, modifier_name),
        }

    def reset_simulation(self, object_name, modifier_name):
        result = self.clear_simulation_cache(object_name, modifier_name)
        result["operation"] = "reset"
        result["frame_unchanged"] = int(bpy.context.scene.frame_current)
        return result

    def bake_simulation(self, object_name, modifier_name, bake_id):
        started = time.time()
        operator_result = self._run_simulation_bake_operator(
            "geometry_node_bake_single", object_name, modifier_name, bake_id
        )
        return {
            "schema": "blender-simulation-operation/1",
            "operation": "bake",
            "status": "completed",
            "bake_id": int(bake_id),
            "operator_result": operator_result,
            "duration_seconds": round(time.time() - started, 3),
            "cancellable": False,
            "verification": self.get_simulation_status(object_name, modifier_name),
        }

    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png"):
        """
        Capture a screenshot of the current 3D viewport and save it to the specified path.

        Parameters:
        - max_size: Maximum size in pixels for the largest dimension of the image
        - filepath: Path where to save the screenshot file
        - format: Image format (png, jpg, etc.)

        Returns success/error status
        """
        try:
            if not filepath:
                return {"error": "No filepath provided"}

            # Find the active 3D viewport
            area = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    break

            if not area:
                return {"error": "No 3D viewport found"}

            # Take screenshot with proper context override
            with bpy.context.temp_override(area=area):
                bpy.ops.screen.screenshot_area(filepath=filepath)

            # Load and resize if needed
            img = bpy.data.images.load(filepath)
            width, height = img.size

            if max(width, height) > max_size:
                scale = max_size / max(width, height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                img.scale(new_width, new_height)

                # Set format and save
                img.file_format = format.upper()
                img.save()
                width, height = new_width, new_height

            # Cleanup Blender image data
            bpy.data.images.remove(img)

            return {
                "success": True,
                "width": width,
                "height": height,
                "filepath": filepath
            }

        except Exception as e:
            return {"error": str(e)}

    def execute_code(self, code, transaction=False, rollback_on_error=True):
        """Execute Python with an optional, explicitly bounded datablock guard."""
        before = _gn_blend_data_ids()
        try:
            # Create a local namespace for execution
            namespace = {"bpy": bpy}

            # Capture stdout during execution, and return it as result
            capture_buffer = io.StringIO()
            with redirect_stdout(capture_buffer):
                exec(code, namespace)

            captured_output = capture_buffer.getvalue()
            after = _gn_blend_data_ids()
            created = [item for pointer, item in after.items() if pointer not in before]
            deleted = [item for pointer, item in before.items() if pointer not in after]
            return {
                "executed": True,
                "result": captured_output,
                "transaction": bool(transaction),
                "changes": {
                    "created": [
                        {"id_type": item.bl_rna.identifier, "name": item.name}
                        for item in sorted(created, key=lambda value: (value.bl_rna.identifier, value.name))
                    ],
                    "deleted": [
                        {"id_type": item.bl_rna.identifier, "name": item.name}
                        for item in deleted
                    ],
                    "modified": "not_detected_by_bounded_python_guard",
                },
                "rollback_scope": "new_datablocks_only" if transaction else "none",
                "non_restorable_effects": [
                    "modified_or_deleted_datablocks",
                    "filesystem_and_network_side_effects",
                    "external_processes_and_render_outputs",
                ] if transaction else [],
            }
        except Exception as e:
            removed = []
            rollback_errors = []
            if transaction and rollback_on_error:
                created = [
                    item for pointer, item in _gn_blend_data_ids().items()
                    if pointer not in before
                ]
                for item in created:
                    try:
                        removed.append({"id_type": item.bl_rna.identifier, "name": item.name})
                        bpy.data.batch_remove(ids=[item])
                    except Exception as rollback_error:
                        rollback_errors.append(str(rollback_error))
            raise BlenderMCPAddonError(
                "bounded_python_rolled_back" if transaction and not rollback_errors else "blender_python_error",
                f"Code execution error: {str(e)}",
                details={
                    "rollback_scope": "new_datablocks_only" if transaction else "none",
                    "removed": removed,
                    "rollback_errors": rollback_errors,
                    "non_restorable_effects": [
                        "modified_or_deleted_datablocks",
                        "filesystem_and_network_side_effects",
                        "external_processes_and_render_outputs",
                    ],
                },
            ) from e
