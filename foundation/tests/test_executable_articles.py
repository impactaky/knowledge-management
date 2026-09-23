from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from federation_support import UI_FIXTURES

ARTICLES_ROOT = UI_FIXTURES / "articles"
DRAFTS_ROOT = UI_FIXTURES / "pending-drafts"

import indexer
import server


def tree_files(nodes) -> dict:
    files = {}
    for node in nodes:
        if node["type"] == "file":
            files[node["rel"]] = node
        else:
            files.update(tree_files(node["children"]))
    return files


def walk_without_marimo(test, nodes) -> dict:
    files = {}
    for node in nodes:
        if node["type"] == "file":
            files[node["rel"]] = node
        else:
            test.assertNotEqual(node["name"], "__marimo__")
            files.update(walk_without_marimo(test, node["children"]))
    return files


class ExecutableArticleIndexerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.theme = self.root / "articles" / "theme"
        shutil.copytree(ARTICLES_ROOT, self.theme)
        (self.root / "CATALOG.md").write_text("## パッケージ\n- [articles](articles/theme/INDEX.md) — Fixture\n")

    def test_collect_article_chunks_uses_only_marimo_markdown_cells(self) -> None:
        docs = indexer.collect_article_chunks({"name": "articles", "root": self.theme})
        sample_docs = [doc for doc in docs if doc["file"].endswith("executable-sample.py")]

        self.assertTrue(sample_docs)
        joined = "\n".join(doc["text"] for doc in sample_docs)
        self.assertIn("EXECUTABLE_PROSE_SENTINEL", joined)
        self.assertNotIn("CODE_ONLY_IDENTIFIER_SENTINEL", joined)
        self.assertEqual(sample_docs[0]["title"], "Fixture Executable Article")
        self.assertTrue(all(doc["package"] == "articles" for doc in sample_docs))
        self.assertTrue(all(Path(doc["file"]).is_absolute() for doc in sample_docs))
        finding = next(doc for doc in sample_docs if doc["section"] == "Finding")
        self.assertEqual(finding["start_line"], 30)
        self.assertGreaterEqual(finding["end_line"], 32)
        self.assertTrue(all(doc["article_type"] == "executable" for doc in sample_docs))

    def test_tree_includes_executable_article_title_and_hides_marimo_directory(self) -> None:
        tree = server.build_markdown_tree(ARTICLES_ROOT, include_py=True)
        files = walk_without_marimo(self, tree)

        self.assertEqual(files["executable-sample.py"]["title"], "Fixture Executable Article")
        self.assertEqual(files["executable-sample.py"]["articleType"], "executable")

    def test_tree_includes_marimo_py_only_in_directories_with_markdown(self) -> None:
        tree = server.build_markdown_tree(UI_FIXTURES, include_py=True)
        files = tree_files(tree)

        self.assertEqual(files["notes/example.py"]["title"], "Example Executable Note")
        self.assertNotIn("notes/plain.py", files)
        self.assertNotIn("standalone/orphan.py", files)

    def test_draft_tree_includes_standalone_executable_article(self) -> None:
        tree = server.build_markdown_tree(
            DRAFTS_ROOT,
            include_py=True,
            require_markdown_dir_for_py=False,
        )
        files = walk_without_marimo(self, tree)

        self.assertEqual(files["draft-only.py"]["title"], "Draft Only Executable")
        self.assertEqual(files["draft-only.py"]["articleType"], "executable")

    def test_catalog_parser_keeps_articles_package_as_tree_source(self) -> None:
        packages = indexer.parse_catalog(self.root / "CATALOG.md")

        self.assertEqual([pkg["name"] for pkg in packages], ["articles"])
        docs = indexer.collect_article_chunks(packages[0])
        self.assertTrue(any(doc["file"].endswith("executable-sample.py") for doc in docs))

    def test_nested_catalog_root_is_owned_by_most_specific_package(self) -> None:
        outer = {"name": "outer", "root": UI_FIXTURES}
        inner = {"name": "articles", "root": ARTICLES_ROOT}
        file_path = ARTICLES_ROOT / "INDEX.md"

        self.assertFalse(indexer.package_owns_file(outer, file_path, [outer, inner]))
        self.assertTrue(indexer.package_owns_file(inner, file_path, [outer, inner]))


class ExecutableArticleServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_roots = server.PACKAGE_ROOTS
        server.PACKAGE_ROOTS = [ARTICLES_ROOT.resolve()]
        self.addCleanup(setattr, server, "PACKAGE_ROOTS", self.original_roots)
        # is_safe_path resolves roots from the live Catalog; point it at the
        # synthetic fixture so frozen views and drafts stay reachable.
        self.enterContext(patch.object(server, "CATALOG_PATH", UI_FIXTURES / "CATALOG.md"))
        self.enterContext(patch.object(server, "DRAFTS_ROOT", DRAFTS_ROOT.resolve()))

    def test_article_endpoint_serves_frozen_view_without_live_launch(self) -> None:
        sample = ARTICLES_ROOT / "executable-sample.py"
        with patch.object(server, "_launch_live_marimo") as launch:
            response = asyncio.run(server.article(str(sample), embed=True))

        self.assertFalse(launch.called)
        self.assertEqual(Path(response.path), server.frozen_view_path(sample))

    def test_missing_frozen_view_is_non_error_html(self) -> None:
        missing = ARTICLES_ROOT / "missing-frozen.py"
        response = asyncio.run(server.article(str(missing), embed=True))

        self.assertEqual(response.status_code, 200)
        self.assertIn("凍結ビュー未生成", response.body.decode("utf-8"))

    def test_draft_article_endpoint_serves_preview_html(self) -> None:
        draft = DRAFTS_ROOT / "draft-only.py"
        with patch.object(server, "DRAFTS_ROOT", DRAFTS_ROOT.resolve()):
            response = asyncio.run(server.article(str(draft), embed=True))

        self.assertEqual(Path(response.path), server.frozen_view_path(draft))

    def test_live_marimo_launch_uses_python_module_and_reuses_existing_process(self) -> None:
        sample = (ARTICLES_ROOT / "executable-sample.py").resolve()
        server.LIVE_MARIMO_PROCESSES.clear()
        self.addCleanup(server.LIVE_MARIMO_PROCESSES.clear)

        class FakeProcess:
            pid = 4242

            def poll(self):
                return None

        with (
            patch.object(server, "_reserve_live_port", return_value=7799),
            patch.object(server.subprocess, "Popen", return_value=FakeProcess()) as popen,
        ):
            first = server._launch_live_marimo(sample)
            second = server._launch_live_marimo(sample)

        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(popen.call_count, 1)
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[:6], [sys.executable, "-m", "marimo", "run", "--sandbox", "--headless"])
        self.assertIn("--no-token", cmd)
        self.assertIn("--host", cmd)
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[-1], str(sample))

    def test_search_response_preserves_new_contract_and_adds_human_snippet(self) -> None:
        sample = (ARTICLES_ROOT / "executable-sample.py").resolve()
        result = {
            "query": "EXECUTABLE_PROSE_SENTINEL",
            "deep": True,
            "semantic_status": "available",
            "index_results": [
                {
                    "package": "articles",
                    "path": str((ARTICLES_ROOT / "INDEX.md").resolve()),
                    "line": 3,
                    "kind": "index",
                    "claim": "- Fixture entry — [sample](executable-sample.py)",
                },
            ],
            "article_results": [
                {
                    "package": "articles",
                    "path": str(sample),
                    "title": "Fixture Executable Article",
                    "section": "Finding",
                    "anchor": "finding",
                    "score": 0.91,
                }
            ],
            "fulltext_results": [],
            "warnings": [],
            "truncated": False,
        }

        from federation_core.locators import bound_response

        result = bound_response(result)
        formatted = server._format_search_response_for_ui(result)

        self.assertEqual(set(formatted), set(result))
        self.assertIn(
            "<mark>EXECUTABLE_PROSE_SENTINEL</mark>",
            formatted["article_results"][0]["snippet"],
        )
        self.assertEqual(formatted["index_results"][0]["kind"], "index")
        self.assertEqual(formatted["limits"], result["limits"])
        self.assertNotIn("snippet", result["article_results"][0])
        self.assertNotIn("chunks", formatted)
        self.assertNotIn("entries", formatted)


if __name__ == "__main__":
    unittest.main()
