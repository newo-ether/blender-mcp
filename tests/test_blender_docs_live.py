"""Opt-in official-site smoke tests.

Set BLENDER_MCP_LIVE_DOCS=1 to run. The normal suite stays deterministic and
offline; this smoke test detects upstream Sphinx/MkDocs layout changes.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.blender_docs import resolve_documentation_context  # noqa: E402
from blender_mcp.blender_docs_retrieval import BlenderDocumentationClient  # noqa: E402


@unittest.skipUnless(
    os.getenv("BLENDER_MCP_LIVE_DOCS") == "1",
    "set BLENDER_MCP_LIVE_DOCS=1 for official-site smoke tests",
)
class BlenderDocumentationLiveTests(unittest.TestCase):
    def test_official_indexes_and_pages(self):
        client = BlenderDocumentationClient()
        cases = [
            ("manual", "Geometry Nodes"),
            ("python_api", "NodeTree"),
            ("release_notes", "Geometry Nodes"),
        ]
        for source, query in cases:
            with self.subTest(source=source):
                context = resolve_documentation_context(
                    version="5.1",
                    language="en",
                    sources=[source],
                )
                search = client.search(context, query=query, limit=1)
                self.assertEqual(search["errors"], [])
                self.assertEqual(search["result_count"], 1)
                result = search["results"][0]
                self.assertTrue(result["url"].startswith("https://"))
                page = client.get_page(
                    context,
                    page=result["path"],
                    source=source,
                    max_chars=500,
                )
                self.assertTrue(page["content"])
                self.assertLessEqual(page["characters"], 500)


if __name__ == "__main__":
    unittest.main()
