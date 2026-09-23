"""Regression contracts for publication, ranking and payload boundaries."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from federation_core import core
from federation_core.locators import bound_response, json_bytes
import indexer
from federation_support import FOUNDATION, load_hermes_provider, make_fixture


class LocatorContractTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"MEILI_URL": "http://fixture.invalid"}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog, self.articles = make_fixture(self.root)

    def backend(self, entries=(), articles=()):
        def request(_url, _method, path, payload=None):
            return (
                {"hits": list(entries if "entries" in path else articles)}
                if path != "/health"
                else {}
            )

        return patch.object(core, "_meili_request", side_effect=request)

    def test_live_claim_and_heading_repair_do_not_require_reindex(self):
        path = self.articles / "article.md"
        hit = {
            "file": str(path),
            "title": "Old title",
            "section": "Removed section",
            "anchor": "removed",
            "_rankingScore": 0.9,
        }
        with self.backend(articles=[hit]):
            (self.articles / "INDEX.md").write_text(
                "- Corrected conditions — [Article](article.md)\n"
            )
            path.write_text('---\nclaims: ["- Corrected conditions — [Article](article.md)"]\n---\n# Current title\n## Current section\nBODY_MARKER\n')
            result = core.search("query", deep=True, catalog_path=self.catalog)
        row = result["article_results"][0]
        self.assertEqual(row["title"], "Current title")
        self.assertEqual(row["section"], "")
        self.assertIn("section:stale_index", row["omitted"])
        self.assertIn("Corrected conditions", row["claim"])
        self.assertNotIn("BODY_MARKER", str(result))
        self.assertNotIn("Old title", str(result))

    def test_cached_claim_removed_or_corrected_is_invalidated_and_moved_claim_relocated(
        self,
    ):
        index = self.articles / "INDEX.md"
        old = index.read_text().splitlines()[-1]
        hit = {"file": str(index), "line": 3, "text": old}
        with self.backend(entries=[hit]):
            index.write_text("# New\n\n\n\n" + old + "\n")
            result = core.search("semantic query", catalog_path=self.catalog)
            self.assertEqual(result["index_results"][0]["line"], 5)
            index.write_text("- corrected — [Article](article.md)\n")
            changed = core.search("semantic query", catalog_path=self.catalog)
            self.assertEqual(changed["index_results"], [])

    def test_nested_catalog_cannot_republish_work_or_regression_data(self):
        outer = self.root / "package"
        outer.mkdir()
        (outer / "INDEX.md").write_text("# Package\n")
        names = [
            "worklogs/run.md",
            "tests/experiments/REPORT.md",
            "drafts/pending.md",
            "studies/topic.md",
            "regression-queries.json",
        ]
        for name in names:
            p = outer / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("PRIVATE_NEEDLE")
        (outer / "worklogs/INDEX.md").write_text("- PRIVATE_NEEDLE\n")
        code = outer / "code.py"
        code.write_text("PRIVATE_NEEDLE = 1\n")
        self.catalog.write_text(
            "# Catalog\n## パッケージ\n- [outer](package/INDEX.md) — outer\n- [nested](package/worklogs/INDEX.md) — nested\n"
        )
        with self.backend(
            entries=[
                {
                    "file": str(outer / "worklogs/INDEX.md"),
                    "text": "- PRIVATE_NEEDLE",
                    "line": 1,
                }
            ]
        ):
            result = core.search("PRIVATE_NEEDLE", deep=True, catalog_path=self.catalog)
        self.assertEqual(result["index_results"], [])
        self.assertEqual(result["fulltext_results"], [])
        grep = core.grep("PRIVATE_NEEDLE", catalog_path=self.catalog)
        self.assertEqual([r["path"] for r in grep["fulltext_results"]], [str(code)])
        docs = [
            d
            for p in indexer.parse_catalog(self.catalog)
            for d in indexer.collect_index_entries(p)
        ]
        self.assertEqual(docs, [])

    def test_index_navigation_cannot_expose_sibling_work_directories(self):
        root = self.root / "rules"
        root.mkdir()
        (self.root / "drafts").mkdir()
        (self.root / "drafts/pending.md").write_text("private")
        (root / "INDEX.md").write_text(
            "- Private needle — [Draft](../drafts/pending.md)\n"
        )
        self.catalog.write_text(
            "# Catalog\n## パッケージ\n- [rules](rules/INDEX.md) — Rules\n"
        )
        with self.backend():
            r = core.search("Private needle", deep=True, catalog_path=self.catalog)
        self.assertEqual(r["index_results"], [])
        self.assertEqual(r["fulltext_results"], [])
        self.assertEqual(core.grep("Private needle", catalog_path=self.catalog)["fulltext_results"], [])
        self.assertEqual(
            indexer.collect_index_entries(indexer.parse_catalog(self.catalog)[0]), []
        )

    def test_first_engine_hit_wins_even_when_later_raw_score_is_larger(self):
        other = self.articles / "other.md"
        other.write_text("# Other\n")
        with (self.articles / "INDEX.md").open("a") as f:
            f.write("- Other claim — [Other](other.md)\n")
        hits = [
            {
                "file": str(self.articles / "article.md"),
                "section": "First",
                "_rankingScore": 0.81,
            },
            {"file": str(other), "_rankingScore": 0.99},
            {
                "file": str(self.articles / "article.md"),
                "section": "Best",
                "_rankingScore": 1,
            },
        ]
        with self.backend(articles=hits):
            r = core.search("query", deep=True, catalog_path=self.catalog)
        self.assertEqual(
            [x["path"] for x in r["article_results"]],
            [str(self.articles / "article.md"), str(other)],
        )
        self.assertEqual(r["article_results"][0]["section"], "First")

    def test_fifty_thousand_character_line_and_oversized_authored_metadata(self):
        marker = "needle " + ("x" * 50000)
        (self.articles / "article.md").write_text(
            '---\nclaims: ["- ' + ("条件" * 3000) + ' — [Article](article.md)"]\n---\n# ' + ("長" * 3000) + "\n## Section\n" + marker + "\n"
        )
        (self.articles / "INDEX.md").write_text(
            "- " + ("条件" * 3000) + " — [Article](article.md)\n"
        )
        with self.backend():
            r = core.grep("needle", catalog_path=self.catalog)
        self.assertEqual(len(r["fulltext_results"]), 1)
        self.assertNotIn(marker, str(r))
        self.assertLessEqual(json_bytes(r), 16384)
        row = r["fulltext_results"][0]
        self.assertLessEqual(json_bytes(row), 2048)
        self.assertIn("claim:byte_limit", row["omitted"])
        self.assertTrue(r["truncated"])
        self.assertNotIn("claim", row)

    def test_whole_response_and_path_budget_do_not_slice_schema_or_paths(self):
        response = {
            "query": "q",
            "deep": True,
            "semantic_status": "available",
            "warnings": [],
            "truncated": False,
            "article_results": [],
            "fulltext_results": [],
            "index_results": [
                {"path": "/" + ("a" * 1800), "title": "x"} for _ in range(20)
            ]
            + [{"path": "/" + ("b" * 3000)}],
        }
        r = bound_response(response)
        self.assertLessEqual(json_bytes(r), 16384)
        import json

        self.assertLessEqual(
            len(json.dumps(r, ensure_ascii=False, indent=2).encode()), 16384
        )
        self.assertGreater(r["omitted"]["candidates"], 0)
        self.assertTrue(
            all(x["path"] == "/" + ("a" * 1800) for x in r["index_results"])
        )
        with self.backend():
            long = core.search("q" * 513, catalog_path=self.catalog)
        self.assertTrue(long["omitted"]["query"])
        self.assertEqual(long["semantic_status"], "not_requested")

    def test_code_and_readme_locators_survive_but_not_their_body(self):
        root = self.root / "rules"
        root.mkdir()
        (root / "INDEX.md").write_text("# Rules\n")
        (root / "README.md").write_text("# Usage\nBODY_NEEDLE\n")
        (root / "run.py").write_text("BODY_NEEDLE = 42\n")
        self.catalog.write_text(
            "# Catalog\n## パッケージ\n- [rules](rules/INDEX.md) — Rules\n"
        )
        with self.backend():
            r = core.grep("BODY_NEEDLE", catalog_path=self.catalog)
        self.assertEqual(
            {Path(x["path"]).name for x in r["fulltext_results"]},
            {"README.md", "run.py"},
        )
        self.assertFalse(
            any("text" in x or "matches" in x for x in r["fulltext_results"])
        )


class ProviderParityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"MEILI_URL": "http://fixture.invalid"}))

    def test_adapters_default_to_hybrid_and_respect_explicit_false(self):
        import asyncio
        import json
        import runpy
        import types
        import server

        provider_module = load_hermes_provider()
        FederationMemoryProvider = provider_module.FederationMemoryProvider

        class FastMCP:
            def __init__(self, *args): pass
            def tool(self): return lambda fn: fn

        fast = types.ModuleType("mcp.server.fastmcp")
        fast.FastMCP = FastMCP
        with tempfile.TemporaryDirectory() as tmp:
            catalog, theme = make_fixture(Path(tmp))
            calls = []
            def backend(url, method, path, payload=None):
                if path.endswith("chunks/search"):
                    calls.append(payload)
                    return {"hits": [{"file": str(theme / "article.md"), "section": "First", "_rankingScore": .9}]}
                return {"hits": []}
            with patch.dict(sys.modules, {"mcp.server.fastmcp": fast}), patch.dict("os.environ", {"FEDERATION_CATALOG": str(catalog)}), patch.object(server, "CATALOG_PATH", catalog), patch.object(core, "_meili_request", side_effect=backend):
                adapter = runpy.run_path(str(FOUNDATION / "integrations/federation-mcp/server.py"))
                hermes = FederationMemoryProvider(str(catalog))
                mcp_result = adapter["federation_search"]("needle")
                hermes_result = json.loads(hermes.handle_tool_call("federation_search", {"query": "needle"}))
                ui = asyncio.run(server.search("needle"))
                self.assertEqual(mcp_result, hermes_result)
                self.assertTrue(ui["deep"])
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(p["hybrid"]["semanticRatio"] == .7 for p in calls))
                for response in [mcp_result, hermes_result]:
                    self.assertNotIn("needle in first chunk", str(response))
                    self.assertFalse(any("snippet" in r for r in response["article_results"]))
                for response in [adapter["federation_search"]("needle", False), json.loads(hermes.handle_tool_call("federation_search", {"query":"needle", "deep":False})), asyncio.run(server.search("needle", False))]:
                    self.assertFalse(response["deep"])
                    self.assertEqual(response["semantic_status"], "not_requested")
                self.assertEqual(len(calls), 3)
        self.assertTrue(provider_module.SEARCH_SCHEMA["parameters"]["properties"]["deep"]["default"])
        self.assertTrue(server.app.openapi()["paths"]["/api/search"]["get"]["parameters"][1]["schema"]["default"])

    def test_actual_adapters_preserve_locator_contract(self):
        import json
        import runpy
        import types
        import server

        FederationMemoryProvider = load_hermes_provider().FederationMemoryProvider

        class FastMCP:
            def __init__(self, *a):
                pass

            def tool(self):
                return lambda fn: fn

        mcp = types.ModuleType("mcp")
        mcpserver = types.ModuleType("mcp.server")
        fast = types.ModuleType("mcp.server.fastmcp")
        fast.FastMCP = FastMCP
        with tempfile.TemporaryDirectory() as tmp:
            catalog, _ = make_fixture(Path(tmp))
            with (
                patch.dict(
                    sys.modules,
                    {"mcp": mcp, "mcp.server": mcpserver, "mcp.server.fastmcp": fast},
                ),
                patch.dict("os.environ", {"FEDERATION_CATALOG": str(catalog)}),
                patch.object(
                    core, "_meili_request", side_effect=RuntimeError("offline")
                ),
            ):
                adapter = runpy.run_path(
                    str(FOUNDATION / "integrations/federation-mcp/server.py")
                )
                mcp_result = adapter["federation_search"]("needle", True)
                hermes = json.loads(
                    FederationMemoryProvider(str(catalog)).handle_tool_call(
                        "federation_search", {"query": "needle", "deep": True}
                    )
                )
                self.assertEqual(mcp_result, hermes)
                ui = server._format_search_response_for_ui(mcp_result)
                self.assertEqual(
                    ui, mcp_result
                )  # no article hit on outage; UI alone may add snippets
                for field in ["index_results", "article_results", "fulltext_results"]:
                    self.assertFalse(
                        any(
                            set(row) & {"text", "body", "matches", "snippet"}
                            for row in mcp_result[field]
                        )
                    )
                self.assertLessEqual(json_bytes(hermes), 16384)


if __name__ == "__main__":
    unittest.main()
