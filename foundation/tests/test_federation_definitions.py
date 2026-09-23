"""Search returns complete current glossary definitions, within provider budgets."""

import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from federation_core import core
from federation_core.locators import bound_response


FOUNDATION = Path(__file__).resolve().parents[1]


def make_fixture(root: Path) -> tuple[Path, Path]:
    """Create a self-contained glossary and article store without live data."""
    package = root / "articles" / "theme"
    package.mkdir(parents=True)
    (package / "INDEX.md").write_text(
        "# Package\n\n- specification claim — [article](article.md)\n",
        encoding="utf-8",
    )
    (package / "article.md").write_text(
        '---\nclaims: ["Authored claim"]\n---\n# Article\n\nSynthetic body.\n',
        encoding="utf-8",
    )
    catalog = root / "CATALOG.md"
    catalog.write_text(
        "# Catalog\n\n## Packages\n\n"
        "- [fixture](articles/theme/INDEX.md) — Synthetic glossary package\n",
        encoding="utf-8",
    )
    return catalog, package


class ContextDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"MEILI_URL": "http://fixture.invalid"}))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.catalog, self.package = make_fixture(self.root)
        self.context = self.package / "CONTEXT.md"
        self.block = "**用語 (Term)**: inline definition\n\nDefinition paragraph.\n_Avoid_: unconditional use."
        self.context.write_text(
            "# Context\n\n" + self.block + "\n\n**Neighbor**:\nNeighbor definition.\n"
            "\n###### Details\nNon-glossary passage.\n", encoding="utf-8",
        )

    def hit(self, **overrides):
        return {"file": str(self.context), "kind": "term", "line": 3,
                "end_line": 7, "text": self.block, "_rankingScore": 1.0, **overrides}

    def backend(self, entries=(), state="available", articles=()):
        def request(_url, _method, path, payload=None):
            if state == "offline":
                raise RuntimeError("offline")
            if path.endswith("chunks/search") and state == "degraded" and "hybrid" in payload:
                raise RuntimeError("embedder offline")
            return {"hits": list(entries if "entries" in path else articles)}
        return patch.object(core, "_meili_request", side_effect=request)

    def search(self, query="Term", deep=False):
        return core.search(query, deep=deep, catalog_path=self.catalog)

    def assert_definition(self, row, block=None, line=3):
        self.assertEqual(row["definition"], self.block if block is None else block)
        self.assertEqual(row["kind"], "context")
        self.assertEqual(row["path"], str(self.context))
        self.assertEqual(row["line"], line)
        self.assertEqual(row["title"], "Context")
        self.assertEqual(row["section"], "用語 (Term)")
        self.assertEqual(row["anchor"], "用語-term")
        self.assertNotIn("claim", row)
        self.assertNotIn("claim:not_authored", row.get("omitted", []))

    def assert_bounded(self, response):
        for indent in (None, 2):
            self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, indent=indent).encode()), 16384)
            for field in ("index_results", "article_results", "fulltext_results"):
                for row in response[field]:
                    self.assertLessEqual(len(json.dumps(row, ensure_ascii=False, indent=indent).encode()), 2048)

    def test_term_and_body_hits_share_complete_definition_on_every_route(self):
        for state in ("available", "degraded", "offline"):
            for deep in (False, True):
                for entries in ([], [self.hit()]):
                    with self.subTest(state=state, deep=deep, cached=bool(entries)), self.backend(entries, state):
                        # 'definition' hits the inline and subsequent paragraph;
                        # the neighboring term must remain a separate row.
                        for query in ("Term", "Definition paragraph", "unconditional use"):
                            response = self.search(query, deep)
                            expected_status = {"available": "available", "degraded": "degraded", "offline": "unavailable"}[state]
                            self.assertEqual(response["semantic_status"], expected_status if deep else "not_requested")
                            self.assertEqual(len(response["index_results"]), 1)
                            self.assert_definition(response["index_results"][0])
                            self.assertEqual(response["fulltext_results"], [])
                            self.assert_bounded(response)
                        response = self.search("definition", deep)
                        self.assertEqual(len(response["index_results"]), 2)
                        self.assert_definition(response["index_results"][0])

    def test_cached_blocks_read_edited_inline_and_multiline_source_and_relocate(self):
        with self.backend([self.hit()]):
            for block in (
                "**用語 (Term)**: corrected inline.\n_Avoid_: new condition.",
                "**用語 (Term)**:\nCurrent multiline.\n\nApplies only here.\n_Avoid_: elsewhere.",
            ):
                self.context.write_text("# Context\n\n\n\n" + block + "\n\n# Other section\nUnrelated.\n")
                response = self.search("cached query absent from source")
                self.assertEqual(len(response["index_results"]), 1)
                self.assert_definition(response["index_results"][0], block, line=5)
                self.assertNotIn("Unrelated", str(response))

    def test_stale_ranges_do_not_hide_other_terms_and_aliases_deduplicate(self):
        alias = self.package / "alias"
        alias.mkdir()
        (alias / "CONTEXT.md").symlink_to(self.context)
        old = self.hit(end_line=100)
        with self.backend([old, self.hit(file=str(alias / "CONTEXT.md"))]):
            self.context.write_text("# Context\n\n**Neighbor**: definition\n\n" + self.block + "\n")
            result = self.search("definition")
        self.assertEqual(len(result["index_results"]), 2)
        self.assert_definition(result["index_results"][0], line=5)
        self.assertEqual(result["index_results"][1]["section"], "Neighbor")

    def test_identical_definition_lines_resolve_to_each_live_term(self):
        self.context.write_text("# Context\n\n**One**:\nShared definition.\n\n**Two**:\nShared definition.\n")
        with self.backend(state="offline"):
            rows = self.search("Shared definition")["index_results"]
        self.assertEqual([row["line"] for row in rows], [3, 6])
        self.assertEqual([row["definition"] for row in rows],
                         ["**One**:\nShared definition.", "**Two**:\nShared definition."])

    def test_deleted_renamed_and_fenced_cached_terms_never_resolve_to_neighbors(self):
        for source in (
            "# Context\n\n**Neighbor**: replacement\n",
            self.block.replace("用語 (Term)", "Renamed"),
            "```markdown\n" + self.block + "\n```\n**Neighbor**: replacement\n",
            "~~~~markdown\n" + self.block + "\n~~~~\n**Neighbor**: replacement\n",
        ):
            with self.subTest(source=source), self.backend([self.hit()]):
                self.context.write_text(source)
                self.assertEqual(self.search("Term")["index_results"], [])

    def test_section_boundaries_and_fenced_examples(self):
        for heading in ("# Next", "## Next", "### Next", "#### Next", "##### Next", "###### Next", "Next\n====", "Next\n----"):
            with self.subTest(heading=heading), self.backend():
                self.context.write_text("# Context\n\n" + self.block + "\n\n" + heading + "\nSection prose.\n")
                self.assert_definition(self.search()["index_results"][0])
        # Code inside a real definition is kept verbatim, without letting its
        # example term or heading split the surrounding authored conditions.
        block = self.block + "\n\n```md\n**Example**: fenced\n# Example heading\n```\n_Avoid_: final condition."
        self.context.write_text("# Context\n\n" + block + "\n\n**Neighbor**: unrelated\n")
        with self.backend():
            self.assert_definition(self.search()["index_results"][0], block)
            self.assertEqual(self.search("Example")["index_results"], [])

    def test_missing_unpublished_inaccessible_and_escaping_sources(self):
        cached = self.hit()
        self.context.unlink()
        with self.backend([cached]):
            self.assertEqual(self.search()["index_results"], [])
        for relative in ("drafts/CONTEXT.md", "tests/CONTEXT.md", "worklogs/CONTEXT.md", ".hidden/CONTEXT.md"):
            hidden = self.package / relative
            hidden.parent.mkdir(exist_ok=True)
            hidden.write_text(self.block)
            self.context.symlink_to(hidden)
            with self.subTest(relative=relative), self.backend([cached, self.hit(file=str(hidden))]):
                self.assertEqual(self.search()["index_results"], [])
            self.context.unlink()
        outside = self.root / "CONTEXT.md"
        outside.write_text(self.block)
        self.context.symlink_to(outside)
        with self.backend([cached]):
            self.assertEqual(self.search()["index_results"], [])
        self.context.unlink()
        self.context.write_text(self.block)
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == self.context:
                raise PermissionError(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", read), self.backend([cached]):
            self.assertEqual(self.search()["index_results"], [])

    def test_definition_is_omitted_whole_at_candidate_budget(self):
        self.context.write_text("# Context\n\n**用語 (Term)**:\n" + "条件" * 2000 + "\n_Avoid_: essential final condition.\n")
        with self.backend(state="offline"):
            result = self.search()
        row = result["index_results"][0]
        self.assertNotIn("definition", row)
        self.assertEqual(row["line"], 3)
        self.assertEqual(row["section"], "用語 (Term)")
        self.assertEqual(row["omitted"], ["definition:byte_limit"])
        self.assertEqual(result["omitted"]["fields"], 1)
        self.assertEqual(result["omitted"]["candidates"], 0)
        self.assertTrue(result["truncated"])
        self.assert_bounded(result)

    def test_indented_serialization_can_force_whole_definition_omission(self):
        with self.backend():
            row = self.search()["index_results"][0]
            padding = 2048 - len(json.dumps(row, ensure_ascii=False).encode())
            block = self.block + "x" * padding
            self.context.write_text("# Context\n\n" + block + "\n")
            row["definition"] = block
            self.assertEqual(len(json.dumps(row, ensure_ascii=False).encode()), 2048)
            self.assertGreater(len(json.dumps(row, ensure_ascii=False, indent=2).encode()), 2048)
            result = self.search()
        self.assertEqual(result["index_results"][0]["omitted"], ["definition:byte_limit"])
        self.assertNotIn("definition", result["index_results"][0])
        self.assert_bounded(result)

    def test_response_budget_omits_definitions_before_article_or_term_locators(self):
        blocks = [f"**Term {i}**:\n" + "条件" * 240 + "\n_Avoid_: final condition." for i in range(10)]
        self.context.write_text("# Context\n\n" + "\n\n".join(blocks))
        articles = []
        for i in range(5):
            article = self.package / f"article-{i}.md"
            article.write_text('---\nclaims: ["Authored claim"]\n---\n# Article\nBODY_SECRET\n')
            articles.append({"file": str(article), "_rankingScore": .9})
        with self.backend(articles=articles):
            result = self.search("Term", deep=True)
        self.assertEqual(len(result["index_results"]), 10)
        self.assertEqual(len(result["article_results"]), 5)
        self.assertEqual([row["path"] for row in result["article_results"]], [hit["file"] for hit in articles])
        self.assertEqual(result["omitted"]["candidates"], 0)
        self.assertTrue(result["truncated"])
        self.assertNotIn("BODY_SECRET", str(result))
        omitted = 0
        for row, block in zip(result["index_results"], blocks):
            if "definition" in row:
                self.assertEqual(row["definition"], block)
            else:
                self.assertEqual(row["omitted"], ["definition:response_byte_limit"])
                omitted += 1
        self.assertGreater(omitted, 0)
        self.assertEqual(result["omitted"]["fields"], omitted)
        self.assert_bounded(result)

    def test_non_glossary_and_explicit_grep_remain_locator_only(self):
        with self.backend():
            row = self.search("Non-glossary")["index_results"][0]
            self.assertNotIn("definition", row)
            grep = core.grep("Definition paragraph", catalog_path=self.catalog)
        self.assertEqual(grep["fulltext_results"][0]["match_lines"], [5])
        self.assertNotIn("definition", grep["fulltext_results"][0])
        self.assertNotIn(self.block, str(grep))

    def test_response_omission_reason_also_fits_candidate_budget(self):
        # The reason can exceed a very short definition; adding it must not
        # push an otherwise valid candidate over its own byte limit.
        row = {"path": "/" + "a" * 1950, "line": 1, "kind": "context", "definition": "**X**: x"}
        self.assertLessEqual(len(json.dumps(row, indent=2).encode()), 2048)
        result = bound_response({
            "query": "X", "deep": False, "semantic_status": "not_requested",
            "warnings": [], "truncated": False, "article_results": [],
            "fulltext_results": [], "index_results": [dict(row) for _ in range(10)],
        })
        self.assertTrue(result["truncated"])
        self.assert_bounded(result)

    def test_unconfigured_backend_returns_live_definitions_without_network(self):
        with patch.dict("os.environ", {"MEILI_URL": ""}), patch.object(core, "_meili_request") as request:
            for deep in (False, True):
                result = self.search(deep=deep)
                self.assert_definition(result["index_results"][0])
                self.assertEqual(result["semantic_status"], "unavailable" if deep else "not_requested")
            request.assert_not_called()

    def test_mcp_preserves_definition_and_describes_contract(self):
        with patch.dict("os.environ", {"FEDERATION_CATALOG": str(self.catalog)}):
            adapter = runpy.run_path(str(FOUNDATION / "integrations/federation-mcp/server.py"))
            for deep in (False, True):
                for state in ("available", "offline"):
                    with self.subTest(deep=deep, state=state), self.backend([self.hit()], state):
                        expected = self.search(deep=deep)
                        self.assert_definition(expected["index_results"][0])
                        self.assertEqual(adapter["federation_search"]("Term", deep), expected)
            self.assertIn("CONTEXT definitions", adapter["federation_search"].__doc__)

    def test_hermes_provider_preserves_complete_definition_on_every_route(self):
        from federation_support import load_hermes_provider

        with patch.dict("os.environ", {"FEDERATION_CATALOG": str(self.catalog)}):
            provider = load_hermes_provider().FederationMemoryProvider(str(self.catalog))
            for deep in (False, True):
                for state in ("available", "offline"):
                    with self.subTest(deep=deep, state=state), self.backend([self.hit()], state):
                        expected = self.search(deep=deep)
                        self.assert_definition(expected["index_results"][0])
                        result = json.loads(
                            provider.handle_tool_call(
                                "federation_search", {"query": "Term", "deep": deep}
                            )
                        )
                        self.assertEqual(result, expected)
                        self.assert_definition(result["index_results"][0])


if __name__ == "__main__":
    unittest.main()
