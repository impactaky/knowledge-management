"""Remote provider protocol tests and real MCP adapter integration."""
import ast
import json
from pathlib import Path
import sys
import subprocess
from unittest.mock import patch

import pytest

from federation_core import grep as core_grep, search as core_search
from federation_support import FOUNDATION, load_hermes_provider, make_fixture

FAKE_SERVER = Path(__file__).parent / "fixtures/federation/fake_mcp.py"


@pytest.fixture
def module():
    return load_hermes_provider()


@pytest.mark.parametrize("mode", ["structured", "text"])
def test_remote_catalog_tools_and_noop_hooks(module, mode):
    config = {"mcp_command": [sys.executable, str(FAKE_SERVER), mode], "meili_url": "http://unused.invalid"}
    with patch.object(module, "_provider_config", return_value=config):
        provider = module.FederationMemoryProvider()
        assert provider.is_available()
        with patch.object(module, "_mcp_call", wraps=module._mcp_call) as call:
            prompt = provider.system_prompt_block()
            assert provider.system_prompt_block() == prompt
            assert call.call_count == 2
        assert prompt == "<federation-catalog>\nSource: remote MCP\n\n# Synthetic remote Catalog\n</federation-catalog>"
        assert {s["name"] for s in provider.get_tool_schemas()} == {"federation_search", "federation_grep", "federation_read"}
        for name, args in [
            ("federation_search", {"query": "fixture", "deep": False}),
            ("federation_grep", {"query": "fixture"}),
            ("federation_read", {"path": "/synthetic/package/guide.md"}),
        ]:
            assert json.loads(provider.handle_tool_call(name, args)) == {"tool": name, "arguments": args}
        assert provider.prefetch("fixture") == ""
        assert provider.sync_turn("fixture", "fixture") is None
        assert provider.on_memory_write("write", "fixture", "fixture") is None


@pytest.mark.parametrize("mode", ["launch", "timeout", "tool_error", "rpc_error", "exit", "malformed"])
def test_remote_failures_are_visible(module, mode):
    command = ["/synthetic/nonexistent-command"] if mode == "launch" else [sys.executable, str(FAKE_SERVER), mode]
    with patch.object(module, "_provider_config", return_value={"mcp_command": command}), patch.object(module, "MCP_TIMEOUT_SECONDS", 0.3):
        provider = module.FederationMemoryProvider()
        assert "federation catalog unavailable" in provider.system_prompt_block()
        result = json.loads(provider.handle_tool_call("federation_grep", {"query": "fixture"}))
        assert result["error"]
        if mode == "timeout":
            assert "timed out" in result["error"]


@pytest.mark.parametrize("command", [[], "ssh example.invalid", [3]])
def test_invalid_command_configuration(module, command):
    with patch.object(module, "_provider_config", return_value={"mcp_command": command}):
        with pytest.raises(ValueError, match="array of strings"):
            module.FederationMemoryProvider().is_available()


def test_conflicting_configuration(module):
    with patch.object(module, "_provider_config", return_value={"catalog_path": "/synthetic/CATALOG.md", "mcp_command": ["fixture"]}):
        provider = module.FederationMemoryProvider()
        with pytest.raises(ValueError, match="mutually exclusive"):
            provider.is_available()
        assert "mutually exclusive" in provider.system_prompt_block()
        assert "mutually exclusive" in json.loads(provider.handle_tool_call("federation_search", {"query": "q"}))["error"]


def test_real_stdio_mcp_adapter(module, tmp_path, monkeypatch):
    catalog, package = make_fixture(tmp_path)
    monkeypatch.setenv("FEDERATION_CATALOG", str(catalog))
    monkeypatch.delenv("MEILI_URL", raising=False)
    command = [sys.executable, str(FOUNDATION / "integrations/federation-mcp/server.py")]
    with patch.object(module, "_provider_config", return_value={"mcp_command": command}):
        provider = module.FederationMemoryProvider()
        assert catalog.read_text().rstrip() in provider.system_prompt_block()
        grep = json.loads(provider.handle_tool_call("federation_grep", {"query": "needle"}))
        assert grep["fulltext_results"]
        assert grep == core_grep("needle", catalog_path=catalog)
        path = package / "article.md"
        assert json.loads(provider.handle_tool_call("federation_read", {"path": str(path)})) == {"path": str(path), "content": path.read_text()}
        search = json.loads(provider.handle_tool_call("federation_search", {"query": "specification", "deep": False}))
        assert search["index_results"]
        assert search == core_search("specification", deep=False, catalog_path=catalog)
        rejected = json.loads(provider.handle_tool_call("federation_read", {"path": str(tmp_path / "missing.md")}))
        assert "error" in rejected


def test_provider_has_no_mcp_import(module):
    tree = ast.parse(Path(module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "mcp" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "mcp"


def test_timeout_covers_blocked_stdin(module):
    command = [sys.executable, str(FAKE_SERVER), "blocked_input"]
    with patch.object(module, "MCP_TIMEOUT_SECONDS", 0.3):
        with pytest.raises(TimeoutError, match="timed out"):
            module._mcp_call(command, "federation_search", {"query": "x" * 1_000_000})


def test_remote_client_runs_without_site_packages(module):
    # -S disables site-packages, including any MCP installation.
    code = (
        "import sys; from pathlib import Path; "
        "sys.path.insert(0, sys.argv[1]); "
        "from federation_support import load_hermes_provider; "
        "m = load_hermes_provider(); "
        "assert 'Synthetic remote Catalog' in "
        "m._mcp_call([sys.executable, '-S', sys.argv[2]], 'get_catalog', {})"
    )
    subprocess.run(
        [sys.executable, "-S", "-c", code, str(Path(__file__).parent), str(FAKE_SERVER)],
        check=True, timeout=10,
    )
