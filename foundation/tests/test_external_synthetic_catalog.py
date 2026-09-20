"""Test that common core, checker, and indexer work against an external synthetic private store."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from federation_core import core
from federation_core.publication import PublicationScope
import check
import indexer

ROOT = Path(__file__).resolve().parents[2]
FOUNDATION = ROOT / "foundation"


class ExternalSyntheticCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.external_store = Path(self.temp_dir.name) / "private-store"
        self.external_store.mkdir()

        # Create private packages
        self.articles = self.external_store / "articles" / "general"
        self.articles.mkdir(parents=True)
        (self.articles / "sample-article.md").write_text(
            '---\nsource: "Synthetic private source"\nclaims:\n  - "Private claim — [sample](sample-article.md)."\n---\n'
            "# Sample Article\n\n## Section\nPrivate content\n",
            encoding="utf-8",
        )

        self.notes = self.external_store / "notes"
        self.notes.mkdir()
        (self.notes / "rule.md").write_text("# Private Rule\n\nRule content\n", encoding="utf-8")

        # Create private catalog referencing shared foundation and private packages
        self.catalog = self.external_store / "CATALOG.md"
        self.catalog.write_text(
            "# Private Store Catalog\n\n"
            "## Packages\n\n"
            f"- [shared-rules]({FOUNDATION}/INDEX.md) — Shared rules and vocabulary.\n"
            "- [articles](articles/) — Private articles.\n"
            "- [notes](notes/rule.md) — Private notes.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_external_catalog(self):
        packages = core.parse_catalog(self.catalog, strict=True)
        self.assertEqual(len(packages), 3)
        names = {p.name for p in packages}
        self.assertEqual(names, {"shared-rules", "articles", "notes"})

        # Verify roots
        roots = {p.name: p.root for p in packages}
        self.assertEqual(roots["shared-rules"], FOUNDATION)
        self.assertEqual(roots["articles"], self.external_store / "articles")
        self.assertEqual(roots["notes"], self.external_store / "notes")

    def test_check_external_catalog(self):
        findings = check.check(self.catalog)
        self.assertEqual(findings, [])

    def test_search_and_grep_on_external_catalog(self):
        # Shallow search finds private article claim
        search_res = core.search("Private claim", deep=False, catalog_path=self.catalog)
        self.assertEqual(search_res["semantic_status"], "not_requested")
        self.assertTrue(search_res["index_results"])
        self.assertEqual(
            search_res["index_results"][0]["path"],
            str(self.articles / "sample-article.md"),
        )

        # Shallow search finds shared glossary definition
        glossary_res = core.search("信頼境界", deep=False, catalog_path=self.catalog)
        self.assertTrue(glossary_res["index_results"])
        self.assertEqual(glossary_res["index_results"][0]["package"], "shared-rules")

        # Grep locates text in private files
        grep_res = core.grep("Private Rule", catalog_path=self.catalog)
        self.assertTrue(grep_res["fulltext_results"])
        paths = [r["path"] for r in grep_res["fulltext_results"]]
        self.assertIn(str(self.notes / "rule.md"), paths)

    def test_indexer_dry_run_on_external_catalog(self):
        packages = indexer.parse_catalog(self.catalog)
        entries = [
            doc
            for pkg in packages
            for collect in (
                indexer.collect_index_entries,
                indexer.collect_term_entries,
                indexer.collect_claim_entries,
            )
            for doc in collect(pkg)
            if indexer.package_owns_file(pkg, doc["file"], packages)
        ]
        self.assertTrue(entries)
        files = {e["file"] for e in entries}
        # Contains both shared entries and private entries
        self.assertIn(str(self.articles / "sample-article.md"), files)
        self.assertIn(str(FOUNDATION / "modules/knowledge-federation/CONTEXT.md"), files)


if __name__ == "__main__":
    unittest.main()
