import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


FOUNDATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FOUNDATION / "tools" / "knowledge-ui"))
import indexer
import server


class IndexRefreshTests(unittest.TestCase):
    def test_same_destination_rejects_concurrent_refresh_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = f"http://fixture/{Path(tmp).name}"
            with indexer.indexing_lock(destination, "test-"):
                with self.assertRaises(indexer.IndexingBusy):
                    with indexer.indexing_lock(destination + "/", "test-"):
                        self.fail("Concurrent writer entered")
                with indexer.indexing_lock(destination, "other-"):
                    pass
            with indexer.indexing_lock(destination, "test-"):
                pass

    def test_collection_change_aborts_before_mutating_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            articles = root / "articles" / "theme"
            articles.mkdir(parents=True)
            catalog = root / "CATALOG.md"
            catalog.write_text("## パッケージ\n- [notes](articles/theme/INDEX.md) — Notes\n")
            claims = articles / "INDEX.md"
            claims.write_text("- Claim — [article](article.md)\n")
            article = articles / "article.md"
            article.write_text("Original body\n")

            def collect_then_change(_):
                article.write_text("Concurrently changed body\n")
                return []

            with (
                patch.object(indexer, "collect_catalog_article_chunks", side_effect=collect_then_change),
                patch.object(indexer, "setup_entries_index") as entries,
                patch.object(indexer, "setup_chunks_index") as chunks,
            ):
                with self.assertRaisesRegex(RuntimeError, "changed during collection"):
                    indexer.main(catalog, f"http://fixture/{root.name}")
            entries.assert_not_called()
            chunks.assert_not_called()

    def test_async_backend_failure_is_not_reported_as_success(self):
        client = Mock()
        client.wait_for_task.return_value = SimpleNamespace(status="failed", error="embedding failed")
        with self.assertRaisesRegex(RuntimeError, "embedding failed"):
            indexer.wait_for_tasks(client, [SimpleNamespace(task_uid=7)])

    def test_long_unicode_sections_keep_all_text_with_bounded_embedding_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "articles" / "theme"
            root.mkdir(parents=True)
            (root / "INDEX.md").write_text("- Claim — [Article](article.md#finding)\n")
            body = "\n".join(["日本語と🦉と数式αβ" * 180, "SECOND_LINE " * 800, "FINAL_SENTINEL"])
            (root / "article.md").write_text(f"# Long article\n\n## Finding\n{body}\n")
            docs = indexer.collect_article_chunks({"root": root, "name": "renamed"})
            fragments = [doc for doc in docs if doc["section"] == "Finding"]
            self.assertGreater(len(fragments), 2)
            self.assertEqual("".join(doc["text"] for doc in fragments), body)
            self.assertEqual(len({doc["id"] for doc in fragments}), len(fragments))
            self.assertTrue(all(doc["anchor"] == "finding" for doc in fragments))
            for doc in docs:
                embedding = f"{doc['title']}\n{doc['section']}\n{doc['text']}"
                self.assertLessEqual(len(embedding.encode()), indexer.EMBEDDING_INPUT_MAX_BYTES)
            self.assertEqual(fragments[0]["start_line"], 4)
            self.assertEqual(fragments[-1]["end_line"], 6)


class IndexStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_requests_share_one_job_and_report_completion(self):
        release = asyncio.Event()

        async def run(*_):
            await release.wait()

        with patch.object(server, "MEILI_URL", "http://fixture"), patch.object(server, "INDEX_TASK", None), patch.object(server.asyncio, "to_thread", side_effect=run) as worker:
            first = await server.reindex()
            second = await server.reindex()
            self.assertEqual(first["status"], "reindexing started")
            self.assertEqual(second["status"], "already running")
            self.assertEqual((await server.reindex_status())["status"], "running")
            release.set()
            await server.INDEX_TASK
            self.assertEqual(worker.await_count, 1)
            self.assertEqual((await server.reindex_status())["status"], "succeeded")

    async def test_failed_job_exposes_error_for_retry(self):
        with patch.object(server, "MEILI_URL", "http://fixture"), patch.object(server, "INDEX_TASK", None), patch.object(server.asyncio, "to_thread", side_effect=RuntimeError("backend failed")):
            await server.reindex()
            await server.INDEX_TASK
            status = await server.reindex_status()
            self.assertEqual(status, {"status": "failed", "error": "backend failed"})


if __name__ == "__main__":
    unittest.main()
