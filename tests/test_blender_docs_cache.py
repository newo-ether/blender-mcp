from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blender_mcp import blender_docs_cache as cache  # noqa: E402
from blender_mcp.blender_docs_retrieval import (  # noqa: E402
    BlenderDocumentationClient,
    BlenderDocumentationRetrievalError,
    FetchedDocument,
)
from blender_mcp.blender_docs import resolve_documentation_context  # noqa: E402


URL_51 = "https://docs.blender.org/manual/en/5.1/index.html"
URL_52 = "https://docs.blender.org/manual/en/dev/index.html"
URL_ZH = "https://docs.blender.org/manual/zh-hans/5.1/index.html"


def document(
    url: str,
    content: bytes = b"<main><h1>Blender</h1></main>",
    *,
    status_code: int = 200,
    etag: str | None = '"etag-1"',
) -> FetchedDocument:
    return FetchedDocument(
        requested_url=url,
        url=url,
        status_code=status_code,
        content_type="text/html" if status_code == 200 else "",
        content=content if status_code == 200 else b"",
        redirects=(),
        etag=etag,
        last_modified="Tue, 14 Jul 2026 08:00:00 GMT",
        cache={"status": "network"},
    )


class FakeNetwork:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(
        self,
        url,
        *,
        accepted_content_types,
        max_bytes,
        request_headers=None,
    ):
        self.calls.append({
            "url": url,
            "accepted": set(accepted_content_types),
            "max_bytes": max_bytes,
            "headers": dict(request_headers or {}),
        })
        if not self.responses:
            raise AssertionError("Unexpected network call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BlenderDocumentationCacheTests(unittest.TestCase):
    def fetch(self, fetcher, url=URL_51):
        return fetcher(
            url,
            accepted_content_types={"text/html"},
            max_bytes=10_000,
        )

    def test_miss_then_fresh_hit_uses_no_second_network_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [1_000.0]
            network = FakeNetwork([document(URL_51)])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
                ttl_seconds=60,
                clock=lambda: now[0],
            )
            first = self.fetch(fetcher)
            now[0] += 30
            second = self.fetch(fetcher)
            self.assertEqual(first.cache["status"], "miss")
            self.assertEqual(second.cache["status"], "hit")
            self.assertFalse(second.cache["stale"])
            self.assertEqual(len(network.calls), 1)
            self.assertEqual(first.content, second.content)

    def test_expired_etag_entry_revalidates_on_304(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [1_000.0]
            network = FakeNetwork([
                document(URL_51),
                document(URL_51, status_code=304, etag='"etag-1"'),
            ])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
                ttl_seconds=10,
                clock=lambda: now[0],
            )
            original = self.fetch(fetcher)
            now[0] += 11
            revalidated = self.fetch(fetcher)
            self.assertEqual(revalidated.cache["status"], "revalidated")
            self.assertEqual(revalidated.content, original.content)
            self.assertEqual(network.calls[1]["headers"]["If-None-Match"], '"etag-1"')
            self.assertIn("If-Modified-Since", network.calls[1]["headers"])

    def test_expired_entry_is_explicit_stale_fallback_when_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [1_000.0]
            offline = BlenderDocumentationRetrievalError(
                "network_error",
                "offline",
                url=URL_51,
            )
            network = FakeNetwork([document(URL_51), offline])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
                ttl_seconds=10,
                clock=lambda: now[0],
            )
            original = self.fetch(fetcher)
            now[0] += 20
            stale = self.fetch(fetcher)
            self.assertEqual(stale.content, original.content)
            self.assertEqual(stale.cache["status"], "stale_fallback")
            self.assertTrue(stale.cache["stale"])
            self.assertEqual(stale.cache["fallback_error"], "network_error")

    def test_404_does_not_use_stale_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [1_000.0]
            missing = BlenderDocumentationRetrievalError(
                "http_error",
                "HTTP 404",
                url=URL_51,
                status_code=404,
            )
            network = FakeNetwork([document(URL_51), missing])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
                ttl_seconds=0,
                clock=lambda: now[0],
            )
            self.fetch(fetcher)
            now[0] += 1
            with self.assertRaises(BlenderDocumentationRetrievalError) as caught:
                self.fetch(fetcher)
            self.assertEqual(caught.exception.status_code, 404)

    def test_version_and_language_urls_have_isolated_cache_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            network = FakeNetwork([
                document(URL_51, b"5.1"),
                document(URL_52, b"dev"),
                document(URL_ZH, b"zh"),
            ])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
            )
            self.assertEqual(self.fetch(fetcher, URL_51).content, b"5.1")
            self.assertEqual(self.fetch(fetcher, URL_52).content, b"dev")
            self.assertEqual(self.fetch(fetcher, URL_ZH).content, b"zh")
            self.assertEqual(len(list(Path(temp_dir).glob("*.json"))), 3)
            self.assertEqual(len(network.calls), 3)

    def test_corrupt_content_hash_self_recovers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            network = FakeNetwork([
                document(URL_51, b"first"),
                document(URL_51, b"recovered"),
            ])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
            )
            self.fetch(fetcher)
            content_path = next(Path(temp_dir).glob("*.bin"))
            content_path.write_bytes(b"corrupt")
            recovered = self.fetch(fetcher)
            self.assertEqual(recovered.content, b"recovered")
            self.assertEqual(recovered.cache["status"], "miss")
            self.assertEqual(len(network.calls), 2)

    def test_future_timestamp_self_recovers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [1_000.0]
            network = FakeNetwork([
                document(URL_51, b"first"),
                document(URL_51, b"recovered"),
            ])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
                clock=lambda: now[0],
            )
            self.fetch(fetcher)
            metadata_path = next(Path(temp_dir).glob("*.json"))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["fetched_at"] = now[0] + 10_000
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            recovered = self.fetch(fetcher)
            self.assertEqual(recovered.content, b"recovered")
            self.assertEqual(len(network.calls), 2)

    def test_unwritable_cache_path_keeps_network_result_with_disclosure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "not-a-directory"
            cache_file.write_text("occupied", encoding="utf-8")
            network = FakeNetwork([document(URL_51)])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=cache_file,
            )
            result = self.fetch(fetcher)
            self.assertEqual(result.cache["status"], "cache_unavailable")
            self.assertEqual(result.content, document(URL_51).content)

    def test_304_without_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            network = FakeNetwork([document(URL_51, status_code=304)])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
            )
            with self.assertRaises(BlenderDocumentationRetrievalError) as caught:
                self.fetch(fetcher)
            self.assertEqual(caught.exception.code, "invalid_not_modified")

    def test_page_response_exposes_cache_and_retrieval_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            page_url = (
                "https://docs.blender.org/manual/en/5.1/"
                "modeling/geometry_nodes/index.html"
            )
            network = FakeNetwork([document(page_url)])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
            )
            client = BlenderDocumentationClient(fetcher)
            context = resolve_documentation_context(
                version="5.1",
                sources=["manual"],
            )
            first = client.get_page(
                context,
                page="modeling/geometry_nodes/index",
                source="manual",
            )
            second = client.get_page(
                context,
                page="modeling/geometry_nodes/index",
                source="manual",
            )
            self.assertEqual(first["cache"]["status"], "miss")
            self.assertEqual(second["cache"]["status"], "hit")
            self.assertEqual(second["retrieval"][0]["status"], "hit")
            self.assertEqual(len(network.calls), 1)

    def test_cache_pruning_respects_global_size_bound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            network = FakeNetwork([
                document(URL_51, b"a" * 200),
                document(URL_52, b"b" * 200),
            ])
            fetcher = cache.CachingOfficialDocsFetcher(
                network,
                cache_root=temp_dir,
                max_cache_bytes=300,
            )
            self.fetch(fetcher, URL_51)
            self.fetch(fetcher, URL_52)
            total = sum(
                path.stat().st_size
                for path in Path(temp_dir).iterdir()
                if path.is_file()
            )
            self.assertLessEqual(total, 300)


if __name__ == "__main__":
    unittest.main()
