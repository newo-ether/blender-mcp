from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import httpx


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp.blender_docs import resolve_documentation_context  # noqa: E402
from blender_mcp import blender_docs_retrieval as retrieval  # noqa: E402


MANUAL_INDEX = b"Search.setIndex(" + json.dumps({
    "docnames": [
        "modeling/geometry_nodes/index",
        "modeling/geometry_nodes/geometry/operations/delete_geometry",
        "editors/shader_editor",
    ],
    "titles": [
        "Geometry Nodes",
        "Delete Geometry Node",
        "Shader Editor",
    ],
    "terms": {
        "geometry": [0, 1],
        "nodes": 0,
        "delete": 1,
        "shader": 2,
    },
    "titleterms": {
        "geometry": [0, 1],
        "delete": 1,
        "shader": 2,
    },
}).encode("utf-8") + b");"

MANUAL_PAGE = b"""<!doctype html>
<html><head><title>Geometry Nodes - Blender Manual</title>
<script>window.secret = 'not readable';</script></head>
<body><nav>Previous Next Navigation</nav><main>
<h1>Geometry Nodes</h1>
<p>Geometry Nodes provides node-based tools for modifying geometry.</p>
<h2>Inputs</h2><p>The input geometry enters through this socket.</p>
<h3>Selection</h3><p>The selection controls affected elements.</p>
<h2>Outputs</h2><p>The modified geometry leaves through this socket.</p>
</main><footer>Copyright and navigation chrome</footer></body></html>"""

DELETE_PAGE = b"""<html><head><title>Delete Geometry Node</title></head><body>
<div role="main"><h1>Delete Geometry Node</h1>
<p>The Delete Geometry node removes selected geometry from the input.</p></div>
</body></html>"""

MKDOCS_INDEX = json.dumps({
    "config": {"lang": ["en"]},
    "docs": [
        {
            "location": "release_notes/5.2/nodes_physics/",
            "title": "Nodes & Physics",
            "text": "The XPBD Solver node adds position based dynamics to Geometry Nodes.",
        },
        {
            "location": "release_notes/5.1/modeling/",
            "title": "Modeling",
            "text": "Changes from another release.",
        },
        {
            "location": "features/unrelated/",
            "title": "Unrelated",
            "text": "XPBD outside release notes must not match.",
        },
    ],
}).encode("utf-8")


class FakeFetcher:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url, *, accepted_content_types, max_bytes):
        self.calls.append((url, max_bytes))
        response = self.responses.get(url)
        if response is None:
            raise retrieval.BlenderDocumentationRetrievalError(
                "fixture_missing",
                f"No fixture for {url}",
                url=url,
            )
        if isinstance(response, Exception):
            raise response
        content, content_type = response
        if content_type not in accepted_content_types:
            raise retrieval.BlenderDocumentationRetrievalError(
                "invalid_content_type",
                "Fixture content type rejected",
                url=url,
            )
        if len(content) > max_bytes:
            raise retrieval.BlenderDocumentationRetrievalError(
                "response_too_large",
                "Fixture too large",
                url=url,
            )
        return retrieval.FetchedDocument(
            requested_url=url,
            url=url,
            status_code=200,
            content_type=content_type,
            content=content,
            redirects=(),
        )


def manual_context():
    return resolve_documentation_context(
        version="5.1",
        sources=["manual"],
    )


class BlenderDocumentationRetrievalTests(unittest.TestCase):
    def test_sphinx_search_ranks_and_extracts_bounded_snippets(self):
        base = "https://docs.blender.org/manual/en/5.1/"
        fetcher = FakeFetcher({
            base + "searchindex.js": (MANUAL_INDEX, "application/javascript"),
            base + "modeling/geometry_nodes/index.html": (MANUAL_PAGE, "text/html"),
            base + "modeling/geometry_nodes/geometry/operations/delete_geometry.html": (
                DELETE_PAGE,
                "text/html",
            ),
        })
        response = retrieval.BlenderDocumentationClient(fetcher).search(
            manual_context(),
            query="geometry nodes",
            limit=2,
        )
        self.assertEqual(response["schema"], "blender-documentation-search/1")
        self.assertEqual(response["result_count"], 2)
        self.assertEqual(response["errors"], [])
        self.assertEqual(response["results"][0]["title"], "Geometry Nodes")
        self.assertIn("Geometry Nodes", response["results"][0]["snippet"])
        self.assertNotIn("Navigation", response["results"][0]["snippet"])
        self.assertTrue(response["results"][0]["url"].startswith(base))

    def test_search_deduplicates_canonical_page_aliases(self):
        duplicate_index = b"Search.setIndex(" + json.dumps({
            "docnames": [
                "modeling/geometry_nodes/index",
                "modeling/geometry_nodes/index",
            ],
            "titles": ["Geometry Nodes", "Geometry Nodes"],
            "terms": {"geometry": [0, 1], "nodes": [0, 1]},
            "titleterms": {"geometry": [0, 1], "nodes": [0, 1]},
        }).encode("utf-8") + b");"
        base = "https://docs.blender.org/manual/en/5.1/"
        fetcher = FakeFetcher({
            base + "searchindex.js": (duplicate_index, "application/javascript"),
            base + "modeling/geometry_nodes/index.html": (MANUAL_PAGE, "text/html"),
        })

        response = retrieval.BlenderDocumentationClient(fetcher).search(
            manual_context(),
            query="Geometry Nodes",
            limit=8,
        )

        self.assertEqual(response["result_count"], 1)
        self.assertEqual(response["results"][0]["title"], "Geometry Nodes")

    def test_snippet_mode_none_defers_page_fetches(self):
        base = "https://docs.blender.org/manual/en/5.1/"
        fetcher = FakeFetcher({base + "searchindex.js": (MANUAL_INDEX, "application/javascript")})
        response = retrieval.BlenderDocumentationClient(fetcher).search(
            manual_context(), query="geometry nodes", limit=2, snippet_mode="none"
        )
        self.assertEqual(response["snippet_mode"], "none")
        self.assertEqual(response["snippet_enriched_count"], 0)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertTrue(all(item["snippet_deferred"] for item in response["results"]))

    def test_page_extracts_exact_heading_section_and_removes_chrome(self):
        base = "https://docs.blender.org/manual/en/5.1/"
        fetcher = FakeFetcher({
            base + "modeling/geometry_nodes/index.html": (MANUAL_PAGE, "text/html"),
        })
        response = retrieval.BlenderDocumentationClient(fetcher).get_page(
            manual_context(),
            page="modeling/geometry_nodes/index",
            source="manual",
            heading="Inputs",
            max_chars=1_000,
        )
        self.assertEqual(response["schema"], "blender-documentation-page/1")
        self.assertEqual(response["heading"], "Inputs")
        self.assertIn("input geometry", response["content"].lower())
        self.assertIn("Selection", response["content"])
        self.assertNotIn("Outputs", response["content"])
        self.assertNotIn("window.secret", response["content"])
        self.assertNotIn("Previous Next", response["content"])

    def test_void_elements_inside_navigation_do_not_hide_document(self):
        extracted = retrieval.extract_html_page(
            b"<body><header><input><img src='x'></header>"
            b"<main><h1>Readable</h1><p>Document body.</p></main></body>"
        )
        self.assertIn("Readable", extracted["content"])
        self.assertIn("Document body", extracted["content"])

    def test_heading_permalink_text_is_removed(self):
        extracted = retrieval.extract_html_page(
            b"<main><h1>Readable<a class='headerlink'>\xc2\xb6</a></h1>"
            b"<p>Body</p></main>"
        )
        self.assertEqual(extracted["headings"], [{"level": 1, "text": "Readable"}])
        self.assertNotIn("\u00b6", extracted["content"])

    def test_page_truncation_never_exceeds_requested_bound(self):
        extracted = retrieval.extract_html_page(
            ("<main><h1>Long</h1><p>" + "word " * 100 + "</p></main>").encode(),
            max_chars=100,
        )
        self.assertTrue(extracted["truncated"])
        self.assertLessEqual(extracted["characters"], 100)
        self.assertTrue(extracted["content"].endswith("…"))

    def test_missing_localized_page_explicitly_falls_back_to_english(self):
        context = resolve_documentation_context(
            version="5.1",
            language="zh_CN",
            sources=["manual"],
        )
        localized = "https://docs.blender.org/manual/zh-hans/5.1/modeling/geometry_nodes/index.html"
        english = "https://docs.blender.org/manual/en/5.1/modeling/geometry_nodes/index.html"
        missing = retrieval.BlenderDocumentationRetrievalError(
            "http_error",
            "Official documentation returned HTTP 404",
            url=localized,
        )
        client = retrieval.BlenderDocumentationClient(FakeFetcher({
            localized: missing,
            english: (MANUAL_PAGE, "text/html"),
        }))
        page = client.get_page(
            context,
            page="modeling/geometry_nodes/index",
            source="manual",
        )
        self.assertEqual(page["language"], "en")
        self.assertEqual(page["url"], english)
        self.assertEqual(page["language_fallback"], {
            "used": True,
            "requested": "zh-hans",
            "resolved": "en",
            "reason": "localized_page_unavailable_uses_english",
        })

    def test_missing_localized_index_explicitly_falls_back_to_english(self):
        context = resolve_documentation_context(
            version="5.1",
            language="zh_CN",
            sources=["manual"],
        )
        localized_index = "https://docs.blender.org/manual/zh-hans/5.1/searchindex.js"
        english_base = "https://docs.blender.org/manual/en/5.1/"
        missing = retrieval.BlenderDocumentationRetrievalError(
            "http_error",
            "Official documentation returned HTTP 404",
            url=localized_index,
        )
        client = retrieval.BlenderDocumentationClient(FakeFetcher({
            localized_index: missing,
            english_base + "searchindex.js": (MANUAL_INDEX, "application/javascript"),
            english_base + "modeling/geometry_nodes/index.html": (MANUAL_PAGE, "text/html"),
        }))
        search = client.search(context, query="Geometry Nodes", limit=1)
        result = search["results"][0]
        self.assertEqual(result["language"], "en")
        self.assertEqual(
            result["language_fallback"]["reason"],
            "localized_index_unavailable_uses_english",
        )

    def test_missing_heading_is_a_precise_error(self):
        with self.assertRaises(retrieval.BlenderDocumentationRetrievalError) as caught:
            retrieval.extract_html_page(
                MANUAL_PAGE,
                heading="Does Not Exist",
            )
        self.assertEqual(caught.exception.code, "heading_not_found")

    def test_release_notes_search_is_version_scoped(self):
        context = resolve_documentation_context(
            version="5.2",
            sources=["release_notes"],
        )
        index_url = "https://developer.blender.org/docs/search/search_index.json"
        client = retrieval.BlenderDocumentationClient(FakeFetcher({
            index_url: (MKDOCS_INDEX, "application/json"),
        }))
        response = client.search(context, query="XPBD", limit=8)
        self.assertEqual(response["result_count"], 1)
        result = response["results"][0]
        self.assertEqual(result["source_version"], "5.2")
        self.assertEqual(result["path"], "nodes_physics/")
        self.assertIn("XPBD Solver", result["snippet"])
        self.assertEqual(
            result["url"],
            "https://developer.blender.org/docs/release_notes/5.2/nodes_physics/",
        )

    def test_one_source_failure_does_not_discard_other_results(self):
        context = resolve_documentation_context(
            version="5.1",
            sources=["manual", "release_notes"],
        )
        base = "https://docs.blender.org/manual/en/5.1/"
        fetcher = FakeFetcher({
            base + "searchindex.js": (MANUAL_INDEX, "application/javascript"),
            base + "modeling/geometry_nodes/index.html": (MANUAL_PAGE, "text/html"),
        })
        response = retrieval.BlenderDocumentationClient(fetcher).search(
            context,
            query="nodes",
            limit=1,
        )
        self.assertEqual(response["result_count"], 1)
        self.assertEqual(response["results"][0]["source"], "manual")
        self.assertEqual(response["errors"][0]["source"], "release_notes")
        self.assertEqual(response["errors"][0]["code"], "fixture_missing")

    def test_malformed_index_is_bounded_per_source_error(self):
        base = "https://docs.blender.org/manual/en/5.1/"
        fetcher = FakeFetcher({
            base + "searchindex.js": (b"not valid javascript", "text/javascript"),
        })
        response = retrieval.BlenderDocumentationClient(fetcher).search(
            manual_context(),
            query="geometry",
        )
        self.assertEqual(response["results"], [])
        self.assertEqual(response["errors"][0]["code"], "malformed_search_index")

    def test_unicode_sphinx_query_is_ranked(self):
        index = {
            "docnames": ["modeling/geometry_nodes/index"],
            "titles": ["几何节点"],
            "terms": {"几何节点": 0},
            "titleterms": {"几何节点": 0},
        }
        ranked = retrieval._rank_sphinx_index(index, "几何节点")
        self.assertEqual(ranked[0]["title"], "几何节点")
        self.assertGreater(ranked[0]["score"], 0)

    def test_empty_or_unreadable_html_is_bounded_error(self):
        for content in (b"", b"<html><script>only code</script></html>"):
            with self.subTest(content=content):
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError) as caught:
                    retrieval.extract_html_page(content)
                self.assertEqual(caught.exception.code, "empty_page")

    def test_page_identifier_rejects_url_and_traversal_forms(self):
        invalid = [
            "https://example.com/page",
            "//example.com/page",
            "/absolute/page",
            "../secret",
            "%2e%2e/secret",
            "%252e%252e/secret",
            "safe\\..\\secret",
            "page?query=yes",
            "page#fragment",
            "page name",
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError):
                    retrieval.normalize_page_identifier(value, "manual")

    def test_query_and_output_bounds_are_validated(self):
        client = retrieval.BlenderDocumentationClient(FakeFetcher({}))
        for query in ("", "x" * 201, "line\nfeed"):
            with self.subTest(query=query):
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError):
                    client.search(manual_context(), query=query)
        for limit in (0, 21, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError):
                    client.search(manual_context(), query="node", limit=limit)
        for max_chars in (99, 50_001, "100"):
            with self.subTest(max_chars=max_chars):
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError):
                    retrieval.extract_html_page(MANUAL_PAGE, max_chars=max_chars)


class OfficialDocsFetcherTests(unittest.TestCase):
    def test_valid_response_and_same_host_redirect(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/start.html"):
                return httpx.Response(302, headers={"location": "final.html"})
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"<main><h1>Final</h1></main>",
            )

        fetcher = retrieval.OfficialDocsFetcher(
            transport=httpx.MockTransport(handler),
        )
        response = fetcher(
            "https://docs.blender.org/manual/en/5.1/start.html",
            accepted_content_types={"text/html"},
            max_bytes=1_000,
        )
        self.assertEqual(
            response.url,
            "https://docs.blender.org/manual/en/5.1/final.html",
        )
        self.assertEqual(len(response.redirects), 1)

    def test_redirect_escape_is_rejected(self):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/private"},
            )
        )
        fetcher = retrieval.OfficialDocsFetcher(transport=transport)
        with self.assertRaises(retrieval.BlenderDocumentationRetrievalError) as caught:
            fetcher(
                "https://docs.blender.org/manual/en/5.1/index.html",
                accepted_content_types={"text/html"},
                max_bytes=1_000,
            )
        self.assertEqual(caught.exception.code, "unsafe_url")

    def test_content_type_status_and_decoded_size_are_enforced(self):
        cases = [
            (
                httpx.Response(404, headers={"content-type": "text/html"}),
                "http_error",
                1_000,
            ),
            (
                httpx.Response(200, headers={"content-type": "image/png"}, content=b"png"),
                "invalid_content_type",
                1_000,
            ),
            (
                httpx.Response(
                    200,
                    headers={
                        "content-type": "text/html",
                        "content-length": "not-a-number",
                    },
                    content=b"x" * 11,
                ),
                "response_too_large",
                10,
            ),
        ]
        for response, expected_code, limit in cases:
            with self.subTest(expected_code=expected_code):
                fetcher = retrieval.OfficialDocsFetcher(
                    transport=httpx.MockTransport(lambda _request, r=response: r),
                )
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError) as caught:
                    fetcher(
                        "https://docs.blender.org/manual/en/5.1/index.html",
                        accepted_content_types={"text/html"},
                        max_bytes=limit,
                    )
                self.assertEqual(caught.exception.code, expected_code)

    def test_non_official_urls_ports_credentials_and_fragments_are_rejected(self):
        invalid = [
            "http://docs.blender.org/manual/en/latest/",
            "https://example.com/manual/",
            "https://user@docs.blender.org/manual/",
            "https://docs.blender.org:444/manual/",
            "https://docs.blender.org/manual/#fragment",
            "https://127.0.0.1/private",
        ]
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError):
                    retrieval.validate_official_url(url)

    def test_timeout_and_network_errors_are_bounded(self):
        failures = [
            (httpx.ReadTimeout("slow"), "timeout"),
            (httpx.ConnectError("offline"), "network_error"),
        ]
        for failure, expected_code in failures:
            with self.subTest(expected_code=expected_code):
                def handler(request, error=failure):
                    error.request = request
                    raise error

                fetcher = retrieval.OfficialDocsFetcher(
                    transport=httpx.MockTransport(handler),
                )
                with self.assertRaises(retrieval.BlenderDocumentationRetrievalError) as caught:
                    fetcher(
                        "https://docs.blender.org/manual/en/5.1/index.html",
                        accepted_content_types={"text/html"},
                        max_bytes=1_000,
                    )
                self.assertEqual(caught.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
