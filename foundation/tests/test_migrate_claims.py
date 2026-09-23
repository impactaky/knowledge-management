"""Lossless claim migration safety while the migrate_claims tool still exists."""
import ast
import json
import sys
import unittest
from contextlib import redirect_stdout
import io
from unittest.mock import patch

from federation_core.articles import markdown_nodes, read_article
from federation_core import migrate_claims as migration
from federation_support import ArticleStoreFixture
import check


class MigrationTests(ArticleStoreFixture, unittest.TestCase):
    def legacy(self):
        self.path.write_text('---\nsource: "ISBN:example ch.2"\n---\n# Article\n## One\nBODY_ONLY\n## Two\nOTHER_BODY\n')
        self.write("articles/INDEX.md", "# Articles\n- [Theme](theme/INDEX.md)\n")
        self.write("articles/theme/INDEX.md", "# Theme\n- Multi — [one](article.md#one), [two](article.md#two) (ISBN:example ch.2)\n- Next — [Article](article.md) (surveyed: 2026-01-01)\n")
        self.write("README.md", "[theme](articles/theme/INDEX.md)\n")

    def test_dry_run_apply_verify_rerun_and_navigation(self):
        self.legacy(); before = self.path.read_bytes()
        plan = migration.build_plan(self.repo)
        self.assertEqual(plan.summary(), {"articles": 1, "claims": 2, "changed_files": 4})
        self.assertEqual(self.path.read_bytes(), before)
        manifest = migration.write_audit(plan, self.root / "audit")
        migration.apply(plan, manifest)
        self.assertEqual(migration.verify(self.repo, manifest)["claims"], 2)
        self.assertFalse((self.path.parent / "INDEX.md").exists())
        self.assertIn("(articles/theme/)", (self.repo / "README.md").read_text())
        self.assertEqual(migration.build_plan(self.repo).changes, [])
        self.assertEqual(read_article(self.path).metadata["source"], "ISBN:example ch.2")
        self.assertEqual(len(read_article(self.path).claims), 2)

    def test_directory_catalog_needs_no_package_index_during_migration(self):
        self.legacy()
        (self.repo / "INDEX.md").unlink()
        (self.repo / "articles/INDEX.md").unlink()
        self.catalog.write_text("## パッケージ\n- [theme](articles/theme/) — Theme\n")
        plan = migration.build_plan(self.repo)
        manifest = migration.write_audit(plan, self.root / "audit")
        migration.apply(plan, manifest)
        self.assertFalse((self.path.parent / "INDEX.md").exists())
        self.assertEqual(migration.verify(self.repo, manifest)["claims"], 2)
        self.assertEqual(check.check(self.catalog), [])
        self.assertEqual(migration.build_plan(self.repo).changes, [])

    def test_conflicting_claims_unknown_reference_and_unlisted_article_abort_without_writes(self):
        self.legacy()
        for mutation in ["claims", "reference", "unlisted"]:
            with self.subTest(mutation=mutation):
                self.legacy()
                if mutation == "claims": self.path.write_text(self.path.read_text().replace("source:", 'claims: ["conflict"]\nsource:'))
                elif mutation == "reference": (self.path.parent / "INDEX.md").write_text("- Bad — [Article](article.md#absent)\n")
                else: self.write("articles/theme/unlisted.md", "# Unlisted\n")
                before = self.path.read_bytes()
                with self.assertRaises(migration.MigrationError): migration.build_plan(self.repo)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertTrue((self.path.parent / "INDEX.md").exists())

    def test_preflight_detects_concurrent_change_and_write_failure_rolls_back(self):
        self.legacy(); plan = migration.build_plan(self.repo)
        manifest = migration.write_audit(plan, self.root / "audit")
        self.path.write_text("concurrent")
        with self.assertRaisesRegex(migration.MigrationError, "changed since planning"): migration.apply(plan, manifest)
        self.path.write_bytes(next(c.before for c in plan.changes if c.path == self.path))
        real = migration._replace; calls = 0
        def fail_second(path, content):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("injected write failure")
            real(path, content)
        with patch.object(migration, "_replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "injected"): migration.apply(plan, manifest)
        for c in plan.changes: self.assertEqual(c.path.read_bytes(), c.before)

    def test_static_marimo_indent_unicode_ast_offsets_and_multiple_cells(self):
        for prefix in ["r", ""]:
            with self.subTest(prefix=prefix):
                source = 'def cell(mo):\n    名称 = "前"; mo.md(' + prefix + '\"\"\"\n    ---\n    source: original\n    ---\n    # One\n    Body \\alpha\n    """)\n    mo.md("## Two\\nSECOND_CELL")\n    mo.md(f"DYNAMIC {danger()}")\n    return 42\n'
                path = self.path.with_suffix(".py")
                claims = ["- 日本語 — [one](article.py#one) (ISBN:example)"]
                after = migration.insert_claims(path, source, claims)
                a, b = read_article(path, source=source), read_article(path, source=after)
                self.assertEqual(a.body, b.body)
                self.assertEqual([n.value for n in markdown_nodes(source)][1:], [n.value for n in markdown_nodes(after)][1:])
                self.assertEqual([c.text for c in b.claims], claims)
                self.assertIn("日本語", after.splitlines()[b.claims[0].line - 1])
                self.assertNotIn("DYNAMIC", b.markdown)
                ast.parse(after)

    def test_catalog_theme_package_index_is_retained_without_claim_inventory(self):
        self.legacy()
        self.catalog.write_text("## パッケージ\n- [theme](articles/theme/INDEX.md) — Theme\n")
        self.write("articles/theme/CONTEXT.md", "# Vocabulary\n")
        with (self.path.parent / "INDEX.md").open("a") as stream:
            stream.write("- [Vocabulary](CONTEXT.md)\n")
        plan = migration.build_plan(self.repo)
        manifest = migration.write_audit(plan, self.root / "audit")
        migration.apply(plan, manifest)
        self.assertEqual((self.path.parent / "INDEX.md").read_text(), "# Theme\n- [Vocabulary](CONTEXT.md)\n")
        self.assertEqual(check.check(self.catalog), [])
        self.assertEqual(migration.build_plan(self.repo).changes, [])

    def test_cli_reports_counts_without_claim_body_and_keeps_separate_audits(self):
        self.legacy()
        audit = self.root / "audits"
        base = ["migrate_claims", "--repo", str(self.repo), "--audit-dir", str(audit)]
        for mode, expected in [("--dry-run", "dry-run"), ("--apply", "applied"), ("--dry-run", "dry-run")]:
            output = io.StringIO()
            with patch.object(sys, "argv", [*base, mode]), redirect_stdout(output):
                self.assertEqual(migration.main(), 0)
            row = json.loads(output.getvalue())
            self.assertEqual(row["status"], expected)
            self.assertEqual(row["claims"], 2)
            self.assertNotIn("Multi", output.getvalue())
            if expected == "applied":
                applied = row["audit"]
        self.assertEqual(row["changed_files"], 0)
        self.assertEqual(len(list(audit.glob("*/manifest.json"))), 3)
        with patch.object(sys, "argv", [*base, "--verify", applied]), redirect_stdout(io.StringIO()):
            self.assertEqual(migration.main(), 0)

    def test_protected_frozen_view_and_symlink_inputs_are_checked(self):
        self.legacy()
        view = self.write("articles/theme/__marimo__/article.html", "FROZEN")
        plan = migration.build_plan(self.repo)
        manifest = migration.write_audit(plan, self.root / "audit")
        migration.apply(plan, manifest)
        self.assertEqual(view.read_text(), "FROZEN")
        view.write_text("CHANGED")
        with self.assertRaisesRegex(migration.MigrationError, "protected content changed"):
            migration.verify(self.repo, manifest)
        (self.path.parent / "alias.md").symlink_to(self.path)
        with self.assertRaisesRegex(migration.MigrationError, "symlink"):
            migration.build_plan(self.repo)


if __name__ == "__main__":
    unittest.main()
