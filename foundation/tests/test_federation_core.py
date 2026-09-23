"""Synthetic core search contracts: contract shape, scope, ranking and adapters."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from federation_core import core
import federation_core
from federation_support import FOUNDATION, make_fixture


class FederationCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict("os.environ", {"MEILI_URL": "http://fixture.invalid"}))

    def test_shallow_response_has_exact_contract_and_only_high_signal_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog, package = make_fixture(Path(tmp))
            result = federation_core.search(
                "specification",
                deep=False,
                catalog_path=catalog,
                meili_url="http://127.0.0.1:1",
            )

        self.assertEqual(set(result), federation_core.PUBLIC_RESPONSE_KEYS)
        self.assertFalse(result["deep"])
        self.assertEqual(result["semantic_status"], "not_requested")
        self.assertEqual(result["article_results"], [])
        self.assertEqual(result["fulltext_results"], [])
        self.assertEqual(len(result["index_results"]), 1)
        self.assertEqual(result["index_results"][0]["path"], str((package / "INDEX.md").resolve()))
        self.assertNotIn("results", result)
        self.assertNotIn("meilisearch_results", result)
        self.assertNotIn("stage1_results", result)

    def test_catalog_is_live_and_catalog_driven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, package = make_fixture(root)
            packages = federation_core.parse_catalog(catalog)
            self.assertEqual([pkg.name for pkg in packages], ["fixture"])
            self.assertEqual(packages[0].root, package.resolve())
            self.assertIn("# CATALOG", federation_core.get_catalog(catalog))
            catalog.write_text("second catalog\n", encoding="utf-8")
            self.assertEqual(federation_core.get_catalog(catalog), "second catalog\n")

    def test_nested_catalog_roots_are_scanned_only_by_most_specific_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outer = root / "outer"
            inner = outer / "inner"
            inner.mkdir(parents=True)
            (outer / "INDEX.md").write_text("# Outer\n", encoding="utf-8")
            (inner / "INDEX.md").write_text("- nested owner\n", encoding="utf-8")
            (inner / "note.txt").write_text("deep nested owner\n", encoding="utf-8")
            catalog = root / "CATALOG.md"
            catalog.write_text(
                "# CATALOG\n\n## パッケージ\n\n"
                "- [outer](outer/INDEX.md) — Outer\n"
                "- [inner](outer/inner/INDEX.md) — Inner\n",
                encoding="utf-8",
            )

            shallow = federation_core.search(
                "nested owner",
                catalog_path=catalog,
                meili_url="http://127.0.0.1:1",
            )
            deep = federation_core.grep("deep nested owner", catalog_path=catalog)

        self.assertEqual(len(shallow["index_results"]), 1)
        self.assertEqual(shallow["index_results"][0]["package"], "inner")
        self.assertEqual(len(deep["fulltext_results"]), 1)
        self.assertEqual(deep["fulltext_results"][0]["package"], "inner")

    def test_semantic_results_are_thresholded_grouped_and_locator_only(self) -> None:
        calls: list[dict | None] = []
        with tempfile.TemporaryDirectory() as tmp:
            catalog, package = make_fixture(Path(tmp))
            article = str((package / "article.md").resolve())

            def fake_request(_url: str, _method: str, path: str, payload=None):
                calls.append(payload)
                if path == "/health":
                    return {"status": "available"}
                if path.endswith("/entries/search"):
                    return {"hits": []}
                if path.endswith("/chunks/search"):
                    return {
                        "hits": [
                            {
                                "package": "fixture",
                                "file": article,
                                "title": "Article",
                                "section": "First",
                                "anchor": "first",
                                "start_line": 6,
                                "end_line": 7,
                                "_rankingScore": 0.81,
                            },
                            {
                                "package": "fixture",
                                "file": article,
                                "title": "Article",
                                "section": "Best",
                                "anchor": "best",
                                "start_line": 9,
                                "end_line": 10,
                                "_rankingScore": 0.93,
                            },
                            {
                                "package": "fixture",
                                "file": str((package / "below.md").resolve()),
                                "title": "Below",
                                "section": "Noise",
                                "anchor": "noise",
                                "_rankingScore": 0.71,
                            },
                        ]
                    }
                raise AssertionError(path)

            with patch.object(federation_core.core, "_meili_request", side_effect=fake_request):
                result = federation_core.search("needle", deep=True, catalog_path=catalog)

        self.assertEqual(result["semantic_status"], "available")
        self.assertEqual(len(result["article_results"]), 1)
        article_result = result["article_results"][0]
        self.assertEqual(
            set(article_result),
            {"package", "path", "title", "section", "anchor", "score", "claim", "claim_source", "links", "line"},
        )
        self.assertEqual(article_result["path"], article)
        self.assertEqual(article_result["section"], "First")
        self.assertEqual(article_result["score"], 0.81)
        self.assertFalse(
            {"summary", "body", "snippet", "text", "content"} & set(article_result)
        )
        hybrid_payload = next(payload for payload in calls if payload and "hybrid" in payload)
        self.assertEqual(
            hybrid_payload["rankingScoreThreshold"],
            federation_core.SEMANTIC_SCORE_THRESHOLD,
        )
        self.assertTrue(hybrid_payload["showRankingScore"])
        self.assertEqual(result["fulltext_results"], [])

    def test_available_semantic_no_match_is_an_empty_success(self) -> None:
        def fake_request(_url: str, _method: str, path: str, payload=None):
            if path == "/health":
                return {"status": "available"}
            return {"hits": []}

        with tempfile.TemporaryDirectory() as tmp:
            catalog, _ = make_fixture(Path(tmp))
            with patch.object(federation_core.core, "_meili_request", side_effect=fake_request):
                result = federation_core.search(
                    "ほわいとぼーど", deep=True, catalog_path=catalog
                )

        self.assertEqual(result["semantic_status"], "available")
        self.assertEqual(result["article_results"], [])

    def test_exact_meili_and_grep_index_position_is_returned_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog, package = make_fixture(Path(tmp))
            index_path = str((package / "INDEX.md").resolve())

            def fake_request(_url: str, _method: str, path: str, payload=None):
                if path == "/health":
                    return {"status": "available"}
                if path.endswith("/entries/search"):
                    return {
                        "hits": [
                            {
                                "package": "fixture",
                                "file": index_path,
                                "line": 3,
                                "kind": "index-entry",
                                "text": "- specification claim — [article](article.md)",
                                "_rankingScore": 1.0,
                            }
                        ]
                    }
                return {"hits": []}

            with patch.object(federation_core.core, "_meili_request", side_effect=fake_request):
                result = federation_core.search("specification", catalog_path=catalog)

        matches = [
            row for row in result["index_results"]
            if row["path"] == index_path and row.get("line") == 3
        ]
        self.assertEqual(len(matches), 1)

    def test_grep_line_inside_meili_term_block_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog, package = make_fixture(Path(tmp))
            context_path = package / "CONTEXT.md"
            context_path.write_text(
                "# Context\n\n**Term**:\ninterior overlap phrase\n",
                encoding="utf-8",
            )

            def fake_request(_url: str, _method: str, path: str, payload=None):
                if path == "/health":
                    return {"status": "available"}
                if path.endswith("/entries/search"):
                    return {
                        "hits": [
                            {
                                "package": "fixture",
                                "file": str(context_path.resolve()),
                                "line": 3,
                                "end_line": 4,
                                "kind": "term",
                                "text": "**Term**:\ninterior overlap phrase",
                                "_rankingScore": 1.0,
                            }
                        ]
                    }
                return {"hits": []}

            with patch.object(federation_core.core, "_meili_request", side_effect=fake_request):
                result = federation_core.search(
                    "interior overlap", deep=True, catalog_path=catalog
                )

        rows = [row for row in result["index_results"] if row["path"] == str(context_path)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 3)
        self.assertFalse(
            any(
                row["path"] == str(context_path)
                and any(line == 4 for line in row["match_lines"])
                for row in result["fulltext_results"]
            )
        )

    def test_semantic_failure_degrades_to_lexical_articles_and_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog, package = make_fixture(Path(tmp))
            article = str((package / "article.md").resolve())

            def fake_request(_url: str, _method: str, path: str, payload=None):
                if path == "/health":
                    return {"status": "available"}
                if path.endswith("/entries/search"):
                    return {"hits": []}
                if payload and "hybrid" in payload:
                    raise RuntimeError("embedder down")
                return {
                    "hits": [
                        {
                            "package": "fixture",
                            "file": article,
                            "title": "Article",
                            "section": "Best",
                            "anchor": "best",
                            "start_line": 6,
                            "end_line": 7,
                            "_rankingScore": 0.9,
                        }
                    ]
                }

            with patch.object(federation_core.core, "_meili_request", side_effect=fake_request):
                result = federation_core.search("needle", deep=True, catalog_path=catalog)

        self.assertEqual(result["semantic_status"], "degraded")
        self.assertTrue(result["article_results"])
        self.assertEqual(result["fulltext_results"], [])
        self.assertTrue(any("semantic search failed" in warning for warning in result["warnings"]))

    def test_stale_meili_hits_outside_catalog_scope_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, _ = make_fixture(root)
            # Real files exist outside every catalog package root, so the
            # result cannot pass merely because a stale path is missing.
            outside = root / "outside"
            outside.mkdir()
            outside_context = outside / "outside-context.md"
            outside_article = outside / "outside-article.md"
            outside_context.write_text("# Outside\n\noutside semantic\n", encoding="utf-8")
            outside_article.write_text("# Outside\n\noutside body\n", encoding="utf-8")

            def fake_request(_url: str, _method: str, path: str, payload=None):
                if path == "/health":
                    return {"status": "available"}
                if path.endswith("/entries/search"):
                    return {
                        "hits": [
                            {
                                "package": "stale",
                                "file": str(outside_context),
                                "line": 3,
                                "kind": "term",
                                "text": "outside semantic",
                                "_rankingScore": 1.0,
                            }
                        ]
                    }
                return {
                    "hits": [
                        {
                            "package": "stale",
                            "file": str(outside_article),
                            "title": "Outside",
                            "section": "Outside",
                            "anchor": "outside",
                            "_rankingScore": 1.0,
                        }
                    ]
                }

            with patch.object(federation_core.core, "_meili_request", side_effect=fake_request):
                result = federation_core.search(
                    "outside semantic", deep=True, catalog_path=catalog
                )

        self.assertEqual(result["article_results"], [])
        self.assertFalse(
            any(row["path"].startswith(str(outside)) for row in result["index_results"])
        )

    def test_all_result_limits_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "pkg"
            package.mkdir()
            (package / "INDEX.md").write_text(
                "\n".join(f"- overflow index {index}" for index in range(12)),
                encoding="utf-8",
            )
            for index in range(12):
                (package / f"file-{index}.txt").write_text(
                    "\n".join(f"overflow match {match}" for match in range(4)),
                    encoding="utf-8",
                )
            catalog = root / "CATALOG.md"
            catalog.write_text(
                "# CATALOG\n\n## パッケージ\n\n"
                "- [fixture](pkg/INDEX.md) — Fixture package\n",
                encoding="utf-8",
            )
            with patch.object(
                federation_core.core, "_meili_request", side_effect=RuntimeError("down")
            ):
                result = federation_core.search("overflow", deep=True, catalog_path=catalog)
                grep = federation_core.grep("overflow", catalog_path=catalog)

        self.assertEqual(len(result["index_results"]), 10)
        self.assertEqual(result["fulltext_results"], [])
        self.assertEqual(len(grep["fulltext_results"]), 10)
        self.assertTrue(all(len(row["match_lines"]) <= 3 for row in grep["fulltext_results"]))
        self.assertTrue(result["truncated"])
        self.assertTrue(grep["truncated"])

    def test_article_limit_is_five_unique_paths_and_sets_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog, package = make_fixture(Path(tmp))
            for index in range(7):
                (package / f"article-{index}.md").write_text(
                    "semantic candidate\n", encoding="utf-8"
                )
            with (package / "INDEX.md").open("a") as stream:
                for index in range(7):
                    stream.write(f"- semantic claim — [Article](article-{index}.md)\n")
            hits = [
                {
                    "package": "fixture",
                    "file": str((package / f"article-{index}.md").resolve()),
                    "title": f"Article {index}",
                    "section": "Section",
                    "anchor": "section",
                    "start_line": 1,
                    "end_line": 1,
                    "_rankingScore": 0.99 - index / 100,
                }
                for index in range(7)
            ]

            def fake_request(_url: str, _method: str, path: str, payload=None):
                if path == "/health":
                    return {"status": "available"}
                if path.endswith("/entries/search"):
                    return {"hits": []}
                return {"hits": hits}

            with patch.object(federation_core.core, "_meili_request", side_effect=fake_request):
                result = federation_core.search("semantic", deep=True, catalog_path=catalog)

        self.assertEqual(len(result["article_results"]), 5)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["fulltext_results"], [])

    def test_grep_skips_hidden_virtualenv_before_stat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog, package = make_fixture(Path(tmp))
            virtualenv_python = package / ".venv.root-broken" / "bin" / "python"
            virtualenv_python.parent.mkdir(parents=True)
            virtualenv_python.write_text("unreadable target", encoding="utf-8")
            original_is_file = Path.is_file

            def guarded_is_file(path: Path) -> bool:
                if ".venv.root-broken" in path.parts:
                    raise PermissionError(path)
                return original_is_file(path)

            with (
                patch.object(Path, "is_file", guarded_is_file),
                patch.object(
                    federation_core.core,
                    "_meili_request",
                    side_effect=RuntimeError("down"),
                ),
            ):
                result = federation_core.grep("needle", catalog_path=catalog)

        self.assertTrue(result["fulltext_results"])

    def test_mcp_server_exposes_registered_read_tools(self) -> None:
        registered: dict[str, object] = {}

        class FastMCP:
            def __init__(self, *args):
                self.tools: dict[str, object] = {}
                registered["instance"] = self

            def tool(self):
                def decorator(function):
                    self.tools[function.__name__] = function
                    return function

                return decorator

        fast = types.ModuleType("mcp.server.fastmcp")
        fast.FastMCP = FastMCP
        with patch.dict(sys.modules, {"mcp.server.fastmcp": fast}):
            adapter = runpy.run_path(str(FOUNDATION / "integrations/federation-mcp/server.py"))
        tools = registered["instance"].tools
        self.assertEqual(set(tools), {"get_catalog", "federation_search", "federation_grep"})
        for name, function in tools.items():
            self.assertIs(adapter[name], function)
            self.assertTrue(function.__doc__)
        search_params = inspect.signature(adapter["federation_search"]).parameters
        self.assertEqual(list(search_params), ["query", "deep"])
        self.assertTrue(search_params["deep"].default)
        self.assertEqual(
            list(inspect.signature(adapter["federation_grep"]).parameters), ["query"]
        )


if __name__ == "__main__":
    unittest.main()
