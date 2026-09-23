"""Article placement, live metadata and publication across repositories."""
from __future__ import annotations

import asyncio
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from federation_core import core
from federation_core.articles import read_article
from federation_core.locators import current_entry, current_locator
from federation_core.publication import PublicationScope
from federation_support import ArticleStoreFixture
import check
import indexer
import server


class ArticleFixture(ArticleStoreFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.scope = PublicationScope(self.repo)

    def backend(self, entries=(), paths=None):
        hits = paths if paths is not None else [self.path]
        def request(url, method, endpoint, payload=None):
            if endpoint == "/health": return {}
            if "/entries/" in endpoint: return {"hits": list(entries)}
            self.assertEqual(payload["limit"], 50)
            self.assertEqual(payload["rankingScoreThreshold"], .72)
            self.assertEqual(payload["hybrid"]["semanticRatio"], .7)
            return {"hits": [{"file": str(p), "section": "Two", "_rankingScore": .9} for p in hits]}
        return patch.object(core, "_meili_request", side_effect=request)


class ArticleOwnedTests(ArticleFixture):
    def test_without_any_article_index_new_articles_are_collected_and_searched(self):
        with self.backend():
            result = core.search("Second", catalog_path=self.catalog)
        self.assertTrue(result["deep"])
        self.assertEqual(result["semantic_status"], "available")
        row = result["article_results"][0]
        self.assertEqual(row["claim"], "- Second — [two](article.md#two)")
        self.assertEqual(row["claim_source"], {"path": str(self.path), "line": 5})
        self.assertNotIn("BODY_ONLY", str(result))
        self.assertFalse(any("claims" in r for f in core.PUBLIC_RESPONSE_KEYS if f.endswith("_results") for r in result[f]))
        self.assertEqual(result["index_results"][0]["kind"], "article-claim")
        self.assertEqual(check.check(self.catalog), [])
        package = indexer.parse_catalog(self.catalog)[0]
        self.assertEqual(len(indexer.collect_claim_entries(package)), 2)
        chunks = indexer.collect_article_chunks(package)
        self.assertTrue(chunks)
        self.assertNotIn("ISBN:example", str(chunks))
        self.assertNotIn("claims:", str(chunks))

    def test_missing_invalid_and_deleted_claims_never_unpublish_surviving_article(self):
        for header, reason in [("", "claim:not_authored"), ("claims: wrong\n", "claim:invalid"), ("claims: [12]\n", "claim:invalid"), ("claims: []\n", "claim:invalid")]:
            with self.subTest(header=header):
                self.path.write_text("---\n" + header + "---\n# Article\n## Two\nBODY_ONLY\n")
                with self.backend():
                    result = core.search("BODY_ONLY", catalog_path=self.catalog)
                row = result["article_results"][0]
                self.assertNotIn("claim", row)
                self.assertIn(reason, row["omitted"])
                self.assertTrue(self.scope.allows_article(self.path))
                self.assertEqual(len(check.check(self.catalog)), 1)

    def test_current_claim_entries_relocate_but_edited_deleted_cached_entries_do_not_survive(self):
        old = read_article(self.path).claims[1]
        entry = {"kind": "article-claim", "file": str(self.path), "line": old.line, "text": old.text}
        self.path.write_text(self.path.read_text().replace("source:", "\n\nsource:"))
        self.assertEqual(current_entry(self.path, entry)[0], old.line + 2)
        self.path.write_text(self.path.read_text().replace("Second", "Corrected"))
        with self.backend(entries=[entry]):
            result = core.search("Corrected", catalog_path=self.catalog)
        self.assertNotIn("Second", str(result))
        self.assertIn("Corrected", result["article_results"][0]["claim"])
        self.assertIn("Corrected", result["index_results"][0]["claim"])
        mock_client = Mock()
        mock_client.index.return_value.search.return_value = {"hits": [entry]}
        with patch.object(server, "CATALOG_PATH", self.catalog), patch.object(server, "meili_client", mock_client):
            self.assertEqual(asyncio.run(server.suggest("Second"))["results"], [])
            current = read_article(self.path).claims[1]
            mock_client.index.return_value.search.return_value = {"hits": [{**entry, "text": current.text}]}
            suggestion = asyncio.run(server.suggest("Corrected"))["results"][0]
            self.assertEqual(suggestion["line"], current.line)
            self.assertEqual(suggestion["claim_source"]["path"], str(self.path))

    def test_draft_move_and_fingerprint_withdraw_stale_hits_and_addition_is_detected(self):
        before = indexer.corpus_fingerprint(self.catalog)
        target = self.repo / "drafts/article.md"; target.parent.mkdir(); self.path.rename(target)
        self.assertNotEqual(before, indexer.corpus_fingerprint(self.catalog))
        self.assertFalse(self.scope.allows_article(self.path))
        with self.backend(paths=[self.path, target]):
            result = core.search("BODY_ONLY", catalog_path=self.catalog)
        self.assertEqual(result["article_results"], [])
        self.assertEqual(result["fulltext_results"], [])
        empty = indexer.corpus_fingerprint(self.catalog)
        self.write("articles/theme/new.md", target.read_text())
        self.assertNotEqual(empty, indexer.corpus_fingerprint(self.catalog))

    def test_nested_and_direct_roots_keep_same_set_without_reopening_exclusions(self):
        for root in [self.repo, self.repo / "articles", self.path.parent]:
            with self.subTest(root=root):
                self.assertEqual(PublicationScope(root).article_publication[1], {self.path})
        for name in ["assets", "drafts", "studies", "books", "tests", ".hidden", "experiments", "evaluations", "worklogs"]:
            excluded = self.write(f"articles/{name}/bad.md", self.path.read_text())
            self.assertFalse(PublicationScope(excluded.parent, boundary=self.repo).allows_article(excluded))
        external = self.root / "outside.md"; external.write_text(self.path.read_text())
        (self.path.parent / "alias.md").symlink_to(external)
        (self.repo / "articles/linked").symlink_to(external.parent, target_is_directory=True)
        self.assertEqual(self.scope.article_publication[1], {self.path})

    def test_five_articles_and_first_engine_section_keep_order(self):
        paths = [self.path] + [self.write(f"articles/theme/other{i}.md", self.path.read_text()) for i in range(6)]
        with self.backend(paths=[paths[3], self.path, self.path, *paths[1:]]):
            result = core.search("q", catalog_path=self.catalog)
        self.assertEqual([r["path"] for r in result["article_results"]], list(map(str, [paths[3], self.path, paths[1], paths[2], paths[4]])))
        self.assertTrue(result["truncated"])
        self.assertEqual(current_locator(self.path, scope=self.scope, section="Unknown")["claim"], read_article(self.path).claims[0].text)

    def test_block_and_flow_yaml_preserve_scalar_values_and_shared_line_entries(self):
        self.path.write_text("---\nclaims:\n  - |\n    first line\n    next line\n  - >-\n    folded\n    value\n---\n# Title\n")
        claims = read_article(self.path).claims
        self.assertEqual([c.text for c in claims], ["first line\nnext line\n", "folded value"])
        self.assertEqual([c.line for c in claims], [3, 6])
        self.path.write_text('---\nclaims: ["One find", "Two find"]\n---\n# Title\n')
        with self.backend(paths=[]):
            result = core.search("find", deep=False, catalog_path=self.catalog)
        self.assertEqual([r["claim"] for r in result["index_results"]], ["One find", "Two find"])
        self.assertEqual(result["semantic_status"], "not_requested")
        self.assertEqual(result["article_results"], [])

    def test_bad_yaml_duplicate_keys_and_aliases_report_errors_without_body_metadata_leak(self):
        for header in ['claims: [yes]', 'claims: [null]', 'claims: [""]', 'claims: [a]\nclaims: [b]', 'claims: &x [a]\ncopy: *x', 'claims: [broken', '? [complex, key]\n: value']:
            with self.subTest(header=header):
                article = read_article(self.path, source="---\n" + header + "\n---\n# Title\n")
                self.assertTrue(article.error)
                self.assertEqual(article.claims, [])
                self.assertEqual(article.body, "# Title\n")


class CheckIsolationTests(ArticleFixture):
    def test_local_only_does_not_read_external_package_or_linked_anchor(self):
        outside = self.root / "private"
        outside.mkdir()
        (outside / "INDEX.md").write_text("# Private\n")
        (outside / "note.md").write_text("# Secret\n")
        with self.catalog.open("a") as stream:
            stream.write(f"- [private]({outside}/INDEX.md) — Private\n")
        self.write("note.md", f"[External]({outside}/note.md#secret)\n")
        read = Path.read_text
        def guard(path, *args, **kwargs):
            if path.is_relative_to(outside):
                raise AssertionError("External source read")
            return read(path, *args, **kwargs)
        with patch.object(Path, "read_text", guard):
            self.assertEqual(check.check(self.catalog, local_only=True), [])


if __name__ == "__main__":
    unittest.main()
