from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from federation_core import core
import indexer
import server


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class SearchPublicationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"MEILI_URL": "http://fixture.invalid"}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = write(self.root, "CATALOG.md", """# CATALOG
## パッケージ
- [foundation](foundation/INDEX.md) — Foundation
- [articles](articles/INDEX.md) — Articles
""")
        write(self.root, "foundation/INDEX.md", "# Foundation\n")
        self.context = write(self.root, "foundation/modules/CONTEXT.md", "**Visible**:\nPUBLIC_TERM\n")
        self.fixture_index = write(self.root, "foundation/tests/fixtures/INDEX.md", "- WORK_ONLY fixture\n")
        write(self.root, "foundation/tests/fixtures/CONTEXT.md", "**Work**:\nWORK_ONLY fixture term\n")
        write(self.root, "foundation/tests/fixtures/body.md", "WORK_ONLY fixture body\n")
        self.article_index = write(self.root, "articles/INDEX.md", """# Articles
- [Theme](theme/INDEX.md)
- [WORK_ONLY reading map](books/reading.md)
- [WORK_ONLY study map](studies/topic.md)
""")
        self.claims = write(self.root, "articles/theme/INDEX.md", """# Theme
- PUBLISHED_CLAIM — [Article](published%20article.md#finding)
- EXECUTABLE_CLAIM — [Notebook](notebook.py)
- WORK_ONLY invalid draft link — [Draft](drafts/pending.md)
```markdown
- WORK_ONLY example claim — [Unstocked](unstocked.md)
```
""")
        self.article = write(self.root, "articles/theme/published article.md", '---\nclaims: ["- PUBLISHED_CLAIM — [Article](published%20article.md#finding)"]\n---\n# Published\n\n## Finding\nPUBLISHED_BODY\n')
        write(self.root, "articles/theme/CONTEXT.md", "**Theme Term**:\nPUBLISHED_THEME_TERM\n")
        self.executable = write(self.root, "articles/theme/notebook.py", 'import marimo as mo\nmo.md("""---\nclaims: [\"- EXECUTABLE_CLAIM — [Notebook](notebook.py)\"]\n---\nEXECUTABLE_BODY""")\n')
        self.unstocked = write(self.root, "articles/theme/drafts/unstocked.md", "WORK_ONLY unstocked body\n")
        self.draft = write(self.root, "articles/theme/drafts/pending.md", "WORK_ONLY draft body\n")
        self.reading = write(self.root, "articles/books/reading.md", "WORK_ONLY reading state\n")
        self.study = write(self.root, "articles/studies/topic.md", "WORK_ONLY study state\n")
        write(self.root, "articles/studies/INDEX.md", "- WORK_ONLY study entry\n")
        write(self.root, "articles/theme/__marimo__/notebook.html", "WORK_ONLY generated view\n")

    def test_indexer_publishes_claimed_articles_and_real_terms_only(self):
        packages = indexer.parse_catalog(self.catalog)
        entries = [doc for pkg in packages for doc in indexer.collect_index_entries(pkg) + indexer.collect_claim_entries(pkg)]
        terms = [doc for pkg in packages for doc in indexer.collect_term_entries(pkg)]
        chunks = indexer.collect_article_chunks(packages[1])

        self.assertEqual({doc["file"] for doc in chunks}, {str(self.article), str(self.executable)})
        self.assertTrue(any("PUBLISHED_BODY" in doc["text"] for doc in chunks))
        self.assertTrue(any("EXECUTABLE_BODY" in doc["text"] for doc in chunks))
        self.assertTrue(any("PUBLISHED_CLAIM" in doc["text"] for doc in entries))
        self.assertTrue(any("PUBLIC_TERM" in doc["text"] for doc in terms))
        self.assertTrue(any("PUBLISHED_THEME_TERM" in doc["text"] for doc in terms))
        self.assertFalse(any("WORK_ONLY" in doc["text"] for doc in entries + terms + chunks))

    def backend(self, _url, _method, endpoint, payload=None):
        if endpoint == "/health":
            return {"status": "available"}
        if endpoint.endswith("/entries/search"):
            return {"hits": [
                {"file": str(self.fixture_index), "text": "WORK_ONLY fixture", "line": 1},
                {"file": str(self.article_index), "text": "- WORK_ONLY — [Map](books/reading.md)", "line": 3},
            ]}
        paths = [
            self.article, self.unstocked, self.draft, self.reading, self.study,
            self.root / "articles/theme/deleted.md", self.claims, self.context,
        ]
        return {"hits": [
            {"file": str(path), "title": path.stem, "section": "Finding", "anchor": "finding", "_rankingScore": 0.99}
            for path in paths
        ]}

    def test_live_search_filters_old_index_hits_and_grep(self):
        with patch.object(core, "_meili_request", side_effect=self.backend):
            result = core.search("WORK_ONLY", deep=True, catalog_path=self.catalog)
        self.assertEqual(result["index_results"], [])
        self.assertEqual(result["fulltext_results"], [])
        self.assertEqual(core.grep("WORK_ONLY", catalog_path=self.catalog)["fulltext_results"], [])
        self.assertEqual([row["path"] for row in result["article_results"]], [str(self.article)])

    def test_draft_move_takes_effect_without_reindex(self):
        with patch.object(core, "_meili_request", side_effect=self.backend):
            before = core.search("PUBLISHED_BODY", deep=True, catalog_path=self.catalog)
            self.article.rename(self.article.parent / "drafts" / self.article.name)
            after = core.search("PUBLISHED_BODY", deep=True, catalog_path=self.catalog)
        self.assertTrue(before["article_results"])
        self.assertEqual(after["article_results"], [])
        self.assertEqual(after["fulltext_results"], [])
        self.assertEqual(core.grep("PUBLISHED_BODY", catalog_path=self.catalog)["fulltext_results"], [])

    def test_independent_grep_keeps_work_files_out(self):
        with patch.object(core, "_meili_request", side_effect=RuntimeError("offline")):
            work = core.grep("WORK_ONLY", catalog_path=self.catalog)
            published = core.grep("PUBLISHED_BODY", catalog_path=self.catalog)
        self.assertEqual(work["fulltext_results"], [])
        self.assertEqual([row["path"] for row in published["fulltext_results"]], [str(self.article)])

    def test_symlinks_cannot_publish_work_files_or_outside_files(self):
        outside = write(self.root, "outside.md", "OUTSIDE_BODY\n")
        for name, target in [("alias.md", self.reading), ("escape.md", outside)]:
            (self.claims.parent / name).symlink_to(target)
            with self.claims.open("a", encoding="utf-8") as stream:
                stream.write(f"- Invalid publication — [{name}]({name})\n")
        chunks = indexer.collect_article_chunks(indexer.parse_catalog(self.catalog)[1])
        self.assertEqual({doc["file"] for doc in chunks}, {str(self.article), str(self.executable)})
        for query in ("WORK_ONLY", "OUTSIDE_BODY"):
            self.assertEqual(core.grep(query, catalog_path=self.catalog)["fulltext_results"], [])

    def test_suggestions_filter_work_entries_before_reindex(self):
        hits = self.backend("", "", "/entries/search")["hits"]
        hits.append({"file": str(self.context), "text": "PUBLIC_TERM", "kind": "term"})
        mock_client = Mock()
        with (
            patch.object(server, "CATALOG_PATH", self.catalog),
            patch.object(server, "meili_client", mock_client),
        ):
            mock_client.index.return_value.search.return_value = {"hits": hits}
            result = asyncio.run(server.suggest("term"))
        self.assertEqual([row["text"] for row in result["results"]], ["PUBLIC_TERM"])

    def test_explicit_fixture_catalog_keeps_its_own_publications(self):
        # Exclusions are relative to the catalog owner, not machine-wide names.
        root = self.root / "foundation/tests/fixtures/isolated/articles/theme"
        write(root, "INDEX.md", "- Local claim — [Article](sample.md)\n")
        sample = write(root, "sample.md", "LOCAL_BODY\n")
        chunks = indexer.collect_article_chunks({"name": "articles", "root": root})
        self.assertEqual({doc["file"] for doc in chunks}, {str(sample)})

    def test_catalog_names_do_not_limit_article_discovery(self):
        self.catalog.write_text(self.catalog.read_text().replace("[articles]", "[personal-notes]"))
        write(self.root, "company/INDEX.md", "- [Article layer](articles/INDEX.md)\n")
        write(self.root, "company/articles/INDEX.md", "- [Chip](chip/INDEX.md)\n")
        write(self.root, "company/articles/chip/INDEX.md", "- COMPANY_CLAIM — [Article](design.md)\n")
        private_article = write(self.root, "company/articles/chip/design.md", "COMPANY_BODY\n")
        write(self.root, "company/internal-note.md", "NOT_AN_ARTICLE\n")
        with self.catalog.open("a") as stream:
            stream.write("- [company-knowledge](company/INDEX.md) — Company\n")
        chunks = indexer.collect_catalog_article_chunks(indexer.parse_catalog(self.catalog))
        self.assertEqual({(doc["file"], doc["package"]) for doc in chunks}, {
            (str(self.article), "personal-notes"),
            (str(self.executable), "personal-notes"),
            (str(private_article), "company-knowledge"),
        })

    def test_nested_article_theme_uses_its_most_specific_catalog_owner(self):
        with self.catalog.open("a") as stream:
            stream.write("- [theme-owner](articles/theme/INDEX.md) — Theme\n")
        chunks = indexer.collect_catalog_article_chunks(indexer.parse_catalog(self.catalog))
        self.assertEqual({doc["package"] for doc in chunks}, {"theme-owner"})
        self.assertEqual(len({doc["id"] for doc in chunks}), len(chunks))
        with patch.object(core, "_meili_request", side_effect=self.backend):
            result = core.search("PUBLISHED_BODY", deep=True, catalog_path=self.catalog)
        self.assertEqual([row["package"] for row in result["article_results"]], ["theme-owner"])

    def test_withdrawal_finishes_before_a_failing_article_update(self):
        client = Mock()
        index = client.index.return_value
        stored = {"published", "work-map"}
        index.get_documents.return_value = SimpleNamespace(
            results=[SimpleNamespace(id=key) for key in stored], total=2,
        )
        index.delete_documents.return_value = SimpleNamespace(task_uid=42)

        def finish_deletion(uid, **_):
            index.add_documents.assert_not_called()
            if uid == 42:
                stored.difference_update(index.delete_documents.call_args.args[0])
            return SimpleNamespace(status="succeeded")

        client.wait_for_task.side_effect = finish_deletion
        index.add_documents.side_effect = RuntimeError("embedding unavailable")
        with self.assertRaisesRegex(RuntimeError, "embedding unavailable"):
            indexer.setup_chunks_index(client, [{"id": "published"}])
        self.assertEqual(stored, {"published"})


if __name__ == "__main__":
    unittest.main()
