"""Independent text locations retain publication, payload and provider contracts."""
import asyncio
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from federation_core import core, grep
from federation_core.locators import bound_response
from federation_support import FOUNDATION, load_hermes_provider, make_fixture
import httpx
import server

GREP_KEYS = {"query", "fulltext_results", "warnings", "truncated", "limits", "omitted"}


class FederationGrepTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"MEILI_URL": "http://fixture.invalid"}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog, self.theme = make_fixture(Path(self.tmp.name))

    def assert_bounded(self, result):
        for indent in (None, 2):
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, indent=indent).encode()), 16384)
            for field in ("index_results", "article_results", "fulltext_results"):
                for row in result.get(field, []):
                    self.assertLessEqual(len(json.dumps(row, ensure_ascii=False, indent=indent).encode()), 2048)
                    self.assertFalse(set(row) & {"body", "text", "snippet", "matches", "claims", "content"})

    def test_search_never_scans_fulltext_in_any_backend_state(self):
        for deep in (False, True):
            for state in ("available", "offline", "semantic_failure", "chunks_failure"):
                with self.subTest(deep=deep, state=state):
                    def request(_url, _method, path, payload=None):
                        if state == "offline":
                            raise RuntimeError("offline")
                        if path.endswith("chunks/search"):
                            if state == "chunks_failure" or (state == "semantic_failure" and "hybrid" in payload):
                                raise RuntimeError("embedding failed")
                            return {"hits": [{"file": str(self.theme / "article.md"), "section": "First", "_rankingScore": .9}]}
                        return {"hits": []}

                    with patch.object(core, "_grep_fulltext_results", side_effect=AssertionError("full scan forbidden")) as scan, patch.object(core, "_meili_request", side_effect=request):
                        result = core.search("specification", deep=deep, catalog_path=self.catalog)
                    scan.assert_not_called()
                    self.assertEqual(result["fulltext_results"], [])
                    self.assertTrue(result["index_results"])
                    expected = "not_requested" if not deep else "unavailable" if state == "offline" else "available" if state == "available" else "degraded"
                    self.assertEqual(result["semantic_status"], expected)
                    self.assertEqual(bool(result["article_results"]), deep and state in {"available", "semantic_failure"})
                    self.assert_bounded(result)

    def test_grep_is_literal_case_insensitive_and_never_calls_search_or_backend(self):
        article = self.theme / "article.md"
        article.write_text(article.read_text() + "\n日本語 Straße [a.*b] exact phrase\n")
        with patch.object(core, "_meili_search", side_effect=AssertionError("backend forbidden")) as backend, patch.object(core, "_meili_request", side_effect=AssertionError("network forbidden")) as network, patch.object(core, "search", side_effect=AssertionError("search forbidden")) as search:
            for query, found in (("NEEDLE", True), ("日本語 STRASSE", True), ("[a.*b]", True), ("a.+b", False), ("phrase exact", False), ("absent", False)):
                with self.subTest(query=query):
                    result = grep(query, catalog_path=self.catalog)
                    self.assertEqual(set(result), GREP_KEYS)
                    self.assertEqual(bool(result["fulltext_results"]), found)
                    self.assertEqual(result["warnings"], [])
                    self.assertFalse(result["truncated"])
                    self.assert_bounded(result)
            with self.assertRaises(FileNotFoundError):
                grep("needle", catalog_path=self.catalog.parent / "missing.md")
        backend.assert_not_called()
        network.assert_not_called()
        search.assert_not_called()

    def test_grep_does_not_suppress_index_claim_or_hybrid_locations(self):
        (self.theme / "INDEX.md").write_text("# Index\n- needle index claim\n")
        (self.theme / "CONTEXT.md").write_text("# Glossary\n**Term**:\nneedle term interior\n")
        article = self.theme / "article.md"
        article.write_text(article.read_text().replace("Authored claim", "needle authored claim"))
        # All locations survive without any entry results or chunk ranges.
        rows = {Path(row["path"]).name: row for row in grep("needle", catalog_path=self.catalog)["fulltext_results"]}
        self.assertEqual(rows["INDEX.md"]["match_lines"], [2])
        self.assertEqual(rows["CONTEXT.md"]["match_lines"], [3])
        self.assertEqual(rows["article.md"]["match_lines"], [2, 7, 10])
        self.assertIn("needle authored claim", rows["article.md"]["claim"])
        self.assertEqual(rows["article.md"]["claim_source"]["line"], 2)

    def test_grep_reads_current_claim_title_and_locations_and_deletion(self):
        article = self.theme / "article.md"
        article.write_text('---\nclaims: ["Current claim"]\n---\n# New title\n## New section\nneedle\n')
        row = grep("needle", catalog_path=self.catalog)["fulltext_results"][0]
        self.assertEqual((row["title"], row["section"], row["anchor"], row["line"]), ("New title", "New section", "new-section", 6))
        self.assertEqual(row["claim"], "Current claim")
        article.write_text("# New title\nneedle\n")
        row = grep("needle", catalog_path=self.catalog)["fulltext_results"][0]
        self.assertIn("claim:not_authored", row["omitted"])
        self.assertNotIn("claim", row)
        article.unlink()
        self.assertEqual(grep("needle", catalog_path=self.catalog)["fulltext_results"], [])

    def test_grep_static_marimo_claims_and_prose_use_real_unique_source_lines(self):
        markdown = '---\nsource: SECRET_METADATA\nclaims: ["Notebook claim"]\n---\n# Notebook\nneedle\nneedle\nneedle\nneedle\n'
        notebook = self.theme / "notebook.py"
        notebook.write_text("raise RuntimeError('must never execute')\nimport marimo as mo\nmo.md(" + repr(markdown) + ")\nCODE_ONLY = 42\n")
        result = grep("needle", catalog_path=self.catalog)
        row = next(r for r in result["fulltext_results"] if r["path"] == str(notebook))
        self.assertEqual(row["match_lines"], [3])
        self.assertEqual(row["claim_source"], {"path": str(notebook), "line": 3})
        self.assertFalse(result["truncated"])
        self.assertEqual(grep("Notebook claim", catalog_path=self.catalog)["fulltext_results"][0]["match_lines"], [3])
        for query in ("CODE_ONLY", "SECRET_METADATA"):
            self.assertEqual(grep(query, catalog_path=self.catalog)["fulltext_results"], [])

    def test_queries_are_validated_before_catalog_or_backend_access(self):
        for operation in (core.search, grep):
            with self.subTest(operation=operation.__name__), patch.object(core, "parse_catalog", side_effect=AssertionError("invalid query executed")) as catalog:
                for query in ("", " \n\t"):
                    with self.assertRaises(ValueError):
                        operation(query, catalog_path=self.catalog)
                for query in ("a" * 513, "界" * 171):
                    result = operation(query, catalog_path=self.catalog)
                    self.assertTrue(result["truncated"])
                    self.assertTrue(result["omitted"]["query"])
                    self.assertEqual(result["query"], "")
                    self.assertTrue(result["warnings"])
                    self.assert_bounded(result)
                catalog.assert_not_called()
            with patch.object(core, "_meili_request", side_effect=RuntimeError("offline")):
                result = operation("界" * 170 + "ab", catalog_path=self.catalog)
                self.assertFalse(result["omitted"]["query"])

    def test_grep_byte_budget_keeps_its_own_envelope(self):
        result = bound_response({
            "query": "界", "warnings": [], "truncated": False,
            "fulltext_results": [
                {"path": "/" + "a" * 1700, "title": "界" * 1000, "match_lines": [1, 2, 3]}
                for _ in range(10)
            ] + [{"path": "/" + "b" * 3000}],
        })
        self.assertEqual(set(result), GREP_KEYS)
        self.assertTrue(result["truncated"])
        self.assertGreater(result["omitted"]["fields"], 0)
        self.assertGreater(result["omitted"]["candidates"], 1)
        self.assert_bounded(result)

    def test_grep_providers_and_http_endpoints_share_contract(self):
        class FastMCP:
            def __init__(self, *args): pass
            def tool(self): return lambda fn: fn
        fast = types.ModuleType("mcp.server.fastmcp")
        fast.FastMCP = FastMCP

        async def requests(adapter, hermes):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture") as client:
                for query in ("needle", "absent", "界" * 171):
                    mcp_result = adapter["federation_grep"](query)
                    raw = hermes.handle_tool_call("federation_grep", {"query": query})
                    ui = await client.get("/api/grep", params={"q": query})
                    self.assertEqual(ui.status_code, 200)
                    self.assertEqual(ui.json(), mcp_result)
                    self.assertEqual(json.loads(raw), mcp_result)
                    self.assertEqual(set(mcp_result), GREP_KEYS)
                    self.assert_bounded(mcp_result)
                for endpoint in ("search", "grep"):
                    for query, status in (("", 422), (" ", 400)):
                        response = await client.get(f"/api/{endpoint}", params={"q": query})
                        self.assertEqual(response.status_code, status)
                for name in ("federation_search", "federation_grep"):
                    with self.assertRaises(ValueError):
                        adapter[name](" ")
                    self.assertIn("error", json.loads(hermes.handle_tool_call(name, {"query": " "})))
                self.catalog.write_text("## パッケージ\n- [bad](https://example.invalid/) — Invalid Catalog\n")
                for endpoint in ("search", "grep"):
                    response = await client.get(f"/api/{endpoint}", params={"q": "needle"})
                    self.assertEqual(response.status_code, 500)

        with patch.dict(sys.modules, {"mcp.server.fastmcp": fast}), patch.dict("os.environ", {"FEDERATION_CATALOG": str(self.catalog)}), patch.object(server, "CATALOG_PATH", self.catalog), patch.object(core, "_meili_request", side_effect=AssertionError("network forbidden")) as network:
            adapter = runpy.run_path(str(FOUNDATION / "integrations/federation-mcp/server.py"))
            hermes = load_hermes_provider().FederationMemoryProvider(str(self.catalog))
            schemas = {s["name"]: s for s in hermes.get_tool_schemas()}
            self.assertEqual(set(schemas), {"federation_search", "federation_grep"})
            self.assertEqual(set(schemas["federation_grep"]["parameters"]["properties"]), {"query"})
            asyncio.run(requests(adapter, hermes))
            network.assert_not_called()
            self.catalog.unlink()
            with self.assertRaises(FileNotFoundError):
                adapter["federation_grep"]("needle")
            with self.assertLogs("hermes_federation_provider", level="ERROR"):
                self.assertIn("error", json.loads(hermes.handle_tool_call("federation_grep", {"query": "needle"})))


if __name__ == "__main__":
    unittest.main()
