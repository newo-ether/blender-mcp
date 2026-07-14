from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from urllib.parse import urlparse


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "blender_mcp"
    / "blender_docs.py"
)
SPEC = importlib.util.spec_from_file_location("blender_docs_test", MODULE_PATH)
docs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(docs)


def detected(
    version=(5, 1, 2),
    version_string="5.1.2",
    version_cycle="release",
) -> dict:
    return {
        "schema": "blender-version-context/1",
        "version": list(version),
        "version_string": version_string,
        "version_cycle": version_cycle,
        "is_prerelease": version_cycle != "release",
        "is_lts": "LTS" in version_string,
        "build": {
            "branch": "blender-v5.1-release",
            "hash": "abc123",
            "date": "2026-06-01",
            "time": "12:34",
            "platform": "Windows",
            "type": "Release",
            "commit_timestamp": 1780317240,
        },
    }


class BlenderDocumentationContextTests(unittest.TestCase):
    def test_stable_auto_uses_exact_minor_channels(self):
        context = docs.resolve_documentation_context(
            detected_blender=detected(),
        )
        self.assertEqual(context["resolved"]["version"], "5.1")
        self.assertFalse(context["resolved"]["is_prerelease"])
        self.assertEqual(
            [item["channel"] for item in context["sources"]],
            ["5.1", "5.1", "5.1"],
        )
        self.assertFalse(any(item["fallback"]["used"] for item in context["sources"]))
        self.assertEqual(context["warnings"], [])

    def test_lts_label_does_not_turn_release_into_prerelease(self):
        context = docs.resolve_documentation_context(
            detected_blender=detected(
                (4, 5, 3),
                "4.5.3 LTS",
                "release",
            ),
        )
        self.assertTrue(context["detected_blender"]["is_lts"])
        self.assertFalse(context["detected_blender"]["is_prerelease"])
        self.assertEqual(context["sources"][0]["channel"], "4.5")

    def test_rc_auto_uses_dev_manual_and_api_but_exact_release_notes(self):
        context = docs.resolve_documentation_context(
            detected_blender=detected(
                (5, 2, 0),
                "5.2.0 LTS Release Candidate",
                "rc",
            ),
        )
        manual, api, notes = context["sources"]
        self.assertEqual(manual["channel"], "dev")
        self.assertEqual(api["channel"], "dev")
        self.assertEqual(notes["channel"], "5.2")
        self.assertTrue(manual["fallback"]["used"])
        self.assertTrue(api["fallback"]["used"])
        self.assertFalse(notes["fallback"]["used"])
        self.assertTrue(context["resolved"]["is_prerelease"])
        self.assertIn("prerelease", context["warnings"][0].lower())

    def test_explicit_version_works_without_blender(self):
        context = docs.resolve_documentation_context(
            version="4.2.3",
            sources=["manual", "api", "release-notes"],
        )
        self.assertIsNone(context["detected_blender"])
        self.assertEqual(context["resolved"]["version"], "4.2")
        self.assertEqual(
            [item["channel"] for item in context["sources"]],
            ["4.2", "4.2", "4.2"],
        )

    def test_current_and_dev_have_source_specific_channels(self):
        current = docs.resolve_documentation_context(version="current")
        self.assertEqual(
            [item["channel"] for item in current["sources"]],
            ["latest", "current", "index"],
        )
        self.assertTrue(current["sources"][2]["fallback"]["used"])

        development = docs.resolve_documentation_context(version="dev")
        self.assertEqual(
            [item["channel"] for item in development["sources"]],
            ["dev", "dev", "index"],
        )

    def test_language_alias_and_english_only_sources_are_explicit(self):
        context = docs.resolve_documentation_context(
            version="5.1",
            language="zh_CN",
        )
        manual, api, notes = context["sources"]
        self.assertEqual(manual["language"], "zh-hans")
        self.assertFalse(manual["fallback"]["used"])
        self.assertEqual(api["language"], "en")
        self.assertEqual(notes["language"], "en")
        self.assertIn("source_is_english_only", api["fallback"]["reasons"])

    def test_unsupported_manual_language_falls_back_to_english(self):
        context = docs.resolve_documentation_context(
            version="5.1",
            language="xx-test",
            sources=["manual"],
        )
        manual = context["sources"][0]
        self.assertEqual(manual["language"], "en")
        self.assertIn(
            "unsupported_language_uses_english",
            manual["fallback"]["reasons"],
        )
        self.assertTrue(context["warnings"])

    def test_sources_are_normalized_deduplicated_and_ordered(self):
        context = docs.resolve_documentation_context(
            version="5.1",
            sources=["api", "manual", "python-api", "releases"],
        )
        self.assertEqual(
            context["requested"]["sources"],
            ["python_api", "manual", "release_notes"],
        )

    def test_every_resolved_url_is_https_on_an_official_host(self):
        for request in ("4.2", "current", "dev"):
            context = docs.resolve_documentation_context(version=request)
            for source in context["sources"]:
                parsed = urlparse(source["base_url"])
                self.assertEqual(parsed.scheme, "https")
                self.assertIn(parsed.hostname, docs.OFFICIAL_DOCUMENTATION_HOSTS)
                self.assertIsNone(parsed.username)
                self.assertIsNone(parsed.password)
                self.assertIsNone(parsed.port)

    def test_invalid_requests_fail_before_any_network_or_blender_access(self):
        for version in ("5", "v5.1", "5.1.2.3", "../dev", "https://example.com"):
            with self.subTest(version=version):
                with self.assertRaises(docs.BlenderDocumentationContextError):
                    docs.resolve_documentation_context(version=version)

        with self.assertRaises(docs.BlenderDocumentationContextError):
            docs.resolve_documentation_context(version="auto")
        with self.assertRaises(docs.BlenderDocumentationContextError):
            docs.resolve_documentation_context(version="5.1", sources=[])
        with self.assertRaises(docs.BlenderDocumentationContextError):
            docs.resolve_documentation_context(version="5.1", sources=["web"])

    def test_detected_version_can_fall_back_to_version_string(self):
        context = docs.resolve_documentation_context(
            detected_blender={
                "version_string": "5.2.0 Alpha",
                "version_cycle": "alpha",
                "build": {},
            }
        )
        self.assertEqual(context["detected_blender"]["version"], [5, 2, 0])
        self.assertEqual(context["sources"][0]["channel"], "dev")


if __name__ == "__main__":
    unittest.main()
