from __future__ import annotations

import unittest

EXPECTED_TOOL_NAMES = {
    "apply_external_dependency_relinks",
    "apply_geometry_node_patch",
    "apply_node_tree_patch",
    "audit_external_dependencies",
    "bake_simulation",
    "claim_blender_instance",
    "clear_simulation_cache",
    "create_node_group",
    "download_polyhaven_asset",
    "download_sketchfab_model",
    "ensure_scene_compositor_tree",
    "ensure_geometry_nodes_modifier",
    "execute_blender_code",
    "export_blender_node_asset",
    "export_geometry_node_tree",
    "export_node_tree",
    "generate_hunyuan3d_model",
    "generate_hyper3d_model_via_images",
    "generate_hyper3d_model_via_text",
    "get_active_blender_instance",
    "get_blender_doc_page",
    "get_blender_documentation_context",
    "get_geometry_node_tree_index",
    "get_geometry_node_type_schema",
    "get_hunyuan3d_status",
    "get_hyper3d_status",
    "get_node_tree_index",
    "get_node_editor_context",
    "get_node_type_schema",
    "get_object_info",
    "get_polyhaven_categories",
    "get_polyhaven_status",
    "get_runtime_automation_context",
    "get_scene_info",
    "get_simulation_status",
    "get_sketchfab_model_preview",
    "get_sketchfab_status",
    "get_viewport_screenshot",
    "import_blender_node_asset",
    "import_generated_asset",
    "import_generated_asset_hunyuan",
    "inspect_evaluated_mesh",
    "list_blender_instances",
    "list_geometry_node_trees",
    "list_node_trees",
    "modify_verify_save",
    "plan_external_dependency_relinks",
    "poll_hunyuan_job_status",
    "poll_rodin_job_status",
    "query_node_graph",
    "release_blender_instance",
    "reset_simulation",
    "search_blender_docs",
    "search_blender_node_assets",
    "search_geometry_node_types",
    "search_polyhaven_assets",
    "search_sketchfab_models",
    "set_texture",
    "validate_geometry_node_patch",
    "validate_node_tree_patch",
}


class ApplicationCompositionTests(unittest.TestCase):
    def test_composition_root_registers_exact_stable_tool_surface(self):
        from blender_mcp.app import mcp

        names = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertEqual(names, EXPECTED_TOOL_NAMES)

    def test_legacy_server_facade_reexports_the_stable_tool_surface(self):
        from blender_mcp import server

        self.assertTrue(EXPECTED_TOOL_NAMES.issubset(server.__all__))
        for name in EXPECTED_TOOL_NAMES:
            with self.subTest(tool=name):
                self.assertTrue(callable(getattr(server, name)))

    def test_mutation_tool_descriptions_route_by_tree_domain(self):
        from blender_mcp.app import mcp

        tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
        expected_routes = {
            "validate_geometry_node_patch": (
                "GeometryNodeTree/NODE_GROUP",
                "validate_node_tree_patch",
            ),
            "apply_geometry_node_patch": (
                "GeometryNodeTree/NODE_GROUP",
                "apply_node_tree_patch",
            ),
            "validate_node_tree_patch": (
                "ShaderNodeTree and CompositorNodeTree",
                "validate_geometry_node_patch",
            ),
            "apply_node_tree_patch": (
                "Shader node-group",
                "apply_geometry_node_patch",
            ),
        }
        for tool_name, phrases in expected_routes.items():
            for phrase in phrases:
                with self.subTest(tool=tool_name, phrase=phrase):
                    self.assertIn(phrase, tools[tool_name].description)


if __name__ == "__main__":
    unittest.main()
