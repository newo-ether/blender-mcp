from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "blender-mcp"
HOST_PATH = ROOT / "src" / "blender_mcp" / "host.py"
PROVIDERS_PATH = ROOT / "src" / "blender_mcp" / "tools" / "providers.py"


def assigned_string(module: ast.Module, name: str) -> str:
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing string assignment: {name}")


def returned_string(module: ast.Module, function_name: str) -> str:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for child in node.body:
                if isinstance(child, ast.Return):
                    return ast.literal_eval(child.value)
    raise AssertionError(f"Missing literal return from: {function_name}")


class BlenderMcpSkillTests(unittest.TestCase):
    def canonical_guidance(self) -> str:
        paths = [
            SKILL_ROOT / "SKILL.md",
            *(SKILL_ROOT / "references").glob("*.md"),
        ]
        return "\n".join(path.read_text(encoding="utf-8") for path in paths)

    def test_canonical_skill_has_portable_frontmatter(self):
        skill_paths = list((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(skill_paths, [SKILL_ROOT / "SKILL.md"])

        text = skill_paths[0].read_text(encoding="utf-8")
        parts = text.split("---", 2)
        self.assertEqual(parts[0], "")
        metadata_lines = [
            line.strip() for line in parts[1].splitlines() if line.strip()
        ]
        self.assertEqual(len(metadata_lines), 2)
        self.assertEqual(metadata_lines[0], "name: blender-mcp")
        self.assertTrue(metadata_lines[1].startswith("description: "))
        self.assertIn("live Blender", metadata_lines[1])
        self.assertIn("purely conceptual", metadata_lines[1])

        body = parts[2].lower()
        for forbidden in ("computer use", "mcp__", "@mcp", "claude", "codex"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)
        self.assertLess(len(text.splitlines()), 500)

    def test_skill_references_exist_and_cover_safe_workflow(self):
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        linked_paths = re.findall(r"\[[^]]+\]\((references/[^)]+)\)", skill_text)
        self.assertEqual(
            set(linked_paths),
            {
                "references/asset-workflows.md",
                "references/node-workflows.md",
                "references/recovery.md",
                "references/render-workflows.md",
            },
        )
        for relative_path in linked_paths:
            self.assertTrue((SKILL_ROOT / relative_path).is_file(), relative_path)

        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [SKILL_ROOT / "SKILL.md", *(SKILL_ROOT / "references").glob("*.md")]
        )
        for required in (
            "get_node_tree_index",
            "export_node_tree",
            "get_node_type_schema",
            "validate_node_tree_patch",
            "apply_node_tree_patch",
            "execute_blender_code",
            "Blender 5.2 List",
            "Uneven Index Field",
            "Do not save",
        ):
            with self.subTest(required=required):
                self.assertIn(required.lower(), combined.lower())

    def test_behavior_fixture_is_covered_by_canonical_guidance(self):
        scenarios = json.loads(
            (ROOT / "tests" / "skill_scenarios.json").read_text(encoding="utf-8")
        )
        expected_ids = {
            "asset_import",
            "conceptual_question",
            "disconnected_blender",
            "live_object_inspection",
            "localized_node_edit",
            "python_fallback",
            "save_safety",
            "uneven_index_list_migration",
            "visual_verification",
        }
        self.assertEqual({scenario["id"] for scenario in scenarios}, expected_ids)
        guidance = self.canonical_guidance().lower()
        for scenario in scenarios:
            self.assertTrue(scenario["prompt"].strip(), scenario["id"])
            for phrase in scenario["required_phrases"]:
                with self.subTest(scenario=scenario["id"], phrase=phrase):
                    self.assertIn(phrase.lower(), guidance)
        conceptual = next(
            scenario for scenario in scenarios if scenario["id"] == "conceptual_question"
        )
        self.assertFalse(conceptual["live_state"])
        self.assertTrue(
            all(
                scenario["live_state"]
                for scenario in scenarios
                if scenario["id"] != "conceptual_question"
            )
        )

    def test_openai_metadata_is_optional_and_invokes_canonical_skill(self):
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn('display_name: "Blender MCP"', metadata)
        self.assertIn('default_prompt: "Use $blender-mcp', metadata)
        self.assertNotIn("icon_", metadata)
        self.assertNotIn("brand_color", metadata)


class McpGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host_module = ast.parse(HOST_PATH.read_text(encoding="utf-8"))
        cls.providers_module = ast.parse(PROVIDERS_PATH.read_text(encoding="utf-8"))

    def test_server_instructions_are_short_and_safe(self):
        guidance = assigned_string(self.host_module, "BLENDER_MCP_INSTRUCTIONS")
        lower = guidance.lower()
        self.assertLess(len(guidance), 1200)
        for required in (
            "smallest useful read-only inspection",
            "structured tools",
            "validate a patch",
            "transactionally",
            "screenshots only when appearance matters",
            "do not save or overwrite",
            "disconnected",
        ):
            with self.subTest(required=required):
                self.assertIn(required, lower)
        for forbidden in ("computer use", "claude", "codex", "always take"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lower)

    def test_asset_prompt_is_optional_and_conditional(self):
        prompt = returned_string(self.providers_module, "asset_creation_strategy")
        lower = prompt.lower()
        self.assertLess(len(prompt), 3000)
        self.assertIn("do not probe every integration", lower)
        self.assertIn("use a viewport screenshot only when appearance", lower)
        self.assertIn("do not", lower)
        self.assertIn("save the .blend file unless the user asks", lower)
        for provider in ("polyhaven", "sketchfab", "hyper3d", "hunyuan3d"):
            with self.subTest(provider=provider):
                self.assertIn(provider, lower)
        for forbidden in (
            "always start by checking",
            "always check the scene",
            "always take a screenshot",
            "before anything",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lower)


if __name__ == "__main__":
    unittest.main()
