from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


FOUNDATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FOUNDATION / "tools" / "knowledge-ui"))
import check as knowledge_check
import indexer


class KnowledgeCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = self.write("CATALOG.md", "## パッケージ\n- [knowledge](knowledge/INDEX.md) — Model\n- [papers](articles/INDEX.md) — Articles\n")
        self.write("knowledge/INDEX.md", "- [Glossary](CONTEXT.md)\n")
        self.write("knowledge/CONTEXT.md", "# Model\n**Term**:\nDefinition\n")
        self.write("articles/INDEX.md", "- [Theme](theme/INDEX.md)\n")
        self.write("articles/theme/INDEX.md", "- Claim — [Article](article.md#part)\n")
        self.write("articles/theme/article.md", '---\nclaims: ["- Claim — [Article](article.md#part)"]\n---\n# Article\n\n## Part\nBody\n')

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_knowledge_ignores_work_files_and_literal_link_examples(self):
        self.write("knowledge/examples.md", "Example: `- [name](missing)` and ``[name](missing-again)``\n````markdown\n[also fake](missing-too)\n````\n")
        self.write("knowledge/tests/fixtures/INDEX.md", "- [fake](missing.md)\n")
        self.write("articles/books/reading.md", "[pending](unwritten.md)\n")
        self.assertEqual(knowledge_check.check(self.catalog), [])

    def test_broken_links_anchors_duplicate_terms_and_orphans_are_reported(self):
        self.write("knowledge/broken.md", "[Missing](missing.md)\n[Bad anchor](../articles/theme/article.md#missing)\n")
        self.write("knowledge/modules/part/CONTEXT.md", "**Term**:\nConflicting definition\n")
        self.write("articles/theme/orphan.md", "Unpublished understanding\n")
        findings = knowledge_check.check(self.catalog)
        self.assertEqual({finding.code for finding in findings}, {
            "missing-link", "missing-anchor", "duplicate-term", "missing-claims",
        })
        self.assertTrue(all(finding.line > 0 for finding in findings))

    def test_separate_contexts_can_define_the_same_word(self):
        self.write("knowledge/contexts/one/CONTEXT.md", "**Term**:\nOne meaning\n")
        self.write("knowledge/contexts/two/CONTEXT.md", "**Term**:\nAnother meaning\n")
        self.assertEqual(knowledge_check.check(self.catalog), [])

    def test_ambiguous_catalog_and_missing_entry_are_reported(self):
        with self.catalog.open("a") as stream:
            stream.write("- [knowledge](other/INDEX.md) — Duplicate name\n")
        self.assertEqual([finding.code for finding in knowledge_check.check(self.catalog)], ["catalog"])
        self.catalog.write_text("## パッケージ\n- [missing](missing/INDEX.md) — Missing\n")
        self.assertEqual([finding.code for finding in knowledge_check.check(self.catalog)], ["missing-entry"])

    def test_incomplete_catalog_cannot_erase_the_index(self):
        self.catalog.write_text("## パッケージ\n- [unfinished\n")
        with patch.object(indexer, "setup_entries_index") as entries, patch.object(indexer, "setup_chunks_index") as chunks:
            with self.assertRaisesRegex(ValueError, "invalid package entry"):
                indexer.main(self.catalog, f"http://fixture/{self.root.name}")
        entries.assert_not_called()
        chunks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
