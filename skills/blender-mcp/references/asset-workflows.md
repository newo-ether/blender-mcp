# Asset workflows

## Choose one relevant source

Do not query every provider. Choose from the requested asset type and the integrations already relevant to the task:

- Blender node assets for reusable Geometry, Shader, or Compositor node groups.
- PolyHaven for HDRIs, textures, and generic models.
- Sketchfab for specific downloadable models.
- Hyper3D or Hunyuan3D for a custom single-object generation request.

Provider status checks are read-only. Downloads, imports, and generation jobs mutate the scene or use an external service.

## Blender node assets

1. Call search_blender_node_assets with a narrow query, tree type, and scope.
2. Use detail=summary for discovery; request detail=full only for shortlisted assets whose interface must be compared.
3. Confirm exact source_path, asset name, tree type, library, scope, interface, and existing-name conflicts.
4. Call export_blender_node_asset for internal graph inspection. Use node_names and neighbor_depth when only one implementation area matters; this disposable read must leave zero appended datablocks.
5. Call import_blender_node_asset only when the requested outcome actually needs the asset in the open file.
6. Keep conflict_policy=REJECT unless the user intentionally wants a separate renamed copy.
7. List or export the imported tree and verify its interface before connecting it to live users.

Search inspection is disposable and should not leave appended datablocks. If import fails, report the server's cleanup result rather than attempting an unbounded manual cleanup.

## PolyHaven

1. Check get_polyhaven_status only when a PolyHaven asset fits the request.
2. Search categories or assets before downloading.
3. Confirm asset identity, type, resolution, format, and the scene mutation implied by import.
4. Use download_polyhaven_asset; use set_texture only after the texture exists and the target object is known.
5. Verify returned object names, material maps, or World assignment with structured inspection.

## Sketchfab

1. Check get_sketchfab_status, then call search_sketchfab_models with downloadable=true.
2. Review license, author, face count, and downloadable status.
3. Use get_sketchfab_model_preview only when visual selection matters.
4. Choose an explicit real-world target_size before download_sketchfab_model.
5. Verify imported names, dimensions, and world bounding box. Correct placement only when required by the user's scene.

## Generated assets

1. Use a generator only for a custom single item, not an entire scene, ground plane, or separately generated fragments of one object.
2. Prefer image-conditioned generation when the user supplied references; otherwise use a focused text description.
3. Start one job, poll its documented status tool without creating duplicate jobs, then import only after completion.
4. Report provider errors, quota, cost, and retry options without silently switching providers.
5. Verify object identity, dimensions, world bounding box, orientation, and placement after import.
6. Reuse or duplicate an existing satisfactory asset instead of generating it again.

## Preserve scene control

- Treat search and preview as selection steps, not permission to import.
- When the user explicitly asked to import or generate, that request authorizes the directly necessary provider action; otherwise confirm first.
- Do not delete rejected imports, replace materials, or change World lighting beyond the requested scope without authorization.
- Do not save the Blender file automatically after import.
