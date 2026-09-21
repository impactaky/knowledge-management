"""Synthetic tests for the Hermes federation memory provider."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FOUNDATION = ROOT / "foundation"
SAMPLE_CATALOG = ROOT / "examples/minimal/CATALOG.md"

# Stub native Hermes agent.memory_provider before importing hermes_federation
if "agent" not in sys.modules:
    sys.modules["agent"] = ModuleType("agent")
if "agent.memory_provider" not in sys.modules:
    mem_mod = ModuleType("agent.memory_provider")
    class MemoryProvider:
        pass
    mem_mod.MemoryProvider = MemoryProvider
    sys.modules["agent.memory_provider"] = mem_mod
    sys.modules["agent"].memory_provider = mem_mod

# Load hermes-federation module
provider_spec = importlib.util.spec_from_file_location(
    "hermes_federation",
    FOUNDATION / "integrations" / "hermes-federation" / "__init__.py",
)
assert provider_spec is not None and provider_spec.loader is not None
hermes_provider_module = importlib.util.module_from_spec(provider_spec)
provider_spec.loader.exec_module(hermes_provider_module)
FederationMemoryProvider = hermes_provider_module.FederationMemoryProvider


class HermesFederationProviderTests(unittest.TestCase):
    def test_prompt_contains_synthetic_catalog(self) -> None:
        provider = FederationMemoryProvider(str(SAMPLE_CATALOG))
        prompt = provider.system_prompt_block()
        self.assertIn("<federation-catalog>", prompt)
        self.assertIn("sample-articles", prompt)
        self.assertTrue(provider.is_available())

    def test_search_and_grep_delegate_to_core(self) -> None:
        provider = FederationMemoryProvider(str(SAMPLE_CATALOG))
        with patch.object(hermes_provider_module, "federation_core_search") as mock_search:
            mock_search.return_value = {
                "query": "window mean",
                "deep": True,
                "semantic_status": "not_requested",
                "index_results": [],
                "article_results": [],
                "fulltext_results": [],
                "warnings": [],
                "truncated": False,
            }
            raw = provider.handle_tool_call(
                "federation_search",
                {"query": "window mean", "deep": True},
            )
            result = json.loads(raw)
            self.assertEqual(result["query"], "window mean")
            mock_search.assert_called_once_with(
                "window mean",
                deep=True,
                catalog_path=SAMPLE_CATALOG.resolve(),
                meili_url="",
            )

    def test_search_passes_configured_meili_url_or_environment(self) -> None:
        provider = FederationMemoryProvider(str(SAMPLE_CATALOG))
        # 1. With configured meili_url
        with patch.object(hermes_provider_module, "_provider_config", return_value={"meili_url": "http://127.0.0.1:7700"}), \
             patch.object(hermes_provider_module, "federation_core_search") as mock_search:
            mock_search.return_value = {"query": "q", "article_results": []}
            provider.handle_tool_call("federation_search", {"query": "q"})
            mock_search.assert_called_once_with("q", deep=True, catalog_path=SAMPLE_CATALOG.resolve(), meili_url="http://127.0.0.1:7700")

        # 2. Unconfigured and no env -> meili_url=""
        with patch.object(hermes_provider_module, "_provider_config", return_value={}), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(hermes_provider_module, "federation_core_search") as mock_search:
            mock_search.return_value = {"query": "q", "article_results": []}
            provider.handle_tool_call("federation_search", {"query": "q"})
            mock_search.assert_called_once_with("q", deep=True, catalog_path=SAMPLE_CATALOG.resolve(), meili_url="")

        # 3. Unconfigured with MEILI_URL env
        with patch.object(hermes_provider_module, "_provider_config", return_value={}), \
             patch.dict("os.environ", {"MEILI_URL": "http://env-meili:7700"}), \
             patch.object(hermes_provider_module, "federation_core_search") as mock_search:
            mock_search.return_value = {"query": "q", "article_results": []}
            provider.handle_tool_call("federation_search", {"query": "q"})
            mock_search.assert_called_once_with("q", deep=True, catalog_path=SAMPLE_CATALOG.resolve(), meili_url="http://env-meili:7700")

        with patch.object(hermes_provider_module, "federation_core_grep") as mock_grep:
            mock_grep.return_value = {
                "query": "UNIT_REQUIRED",
                "fulltext_results": [],
                "warnings": [],
                "truncated": False,
            }
            raw = provider.handle_tool_call(
                "federation_grep",
                {"query": "UNIT_REQUIRED"},
            )
            result = json.loads(raw)
            self.assertEqual(result["query"], "UNIT_REQUIRED")
            mock_grep.assert_called_once_with(
                "UNIT_REQUIRED",
                catalog_path=SAMPLE_CATALOG.resolve(),
            )

    def test_schemas_are_read_only(self) -> None:
        provider = FederationMemoryProvider(str(SAMPLE_CATALOG))
        schemas = provider.get_tool_schemas()
        self.assertEqual(len(schemas), 2)
        names = {s["name"] for s in schemas}
        self.assertEqual(names, {"federation_search", "federation_grep"})
        for schema in schemas:
            self.assertIn("description", schema)
            self.assertIn("parameters", schema)


if __name__ == "__main__":
    unittest.main()
