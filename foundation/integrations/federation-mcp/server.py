#!/usr/bin/env python3
"""stdio MCP server for the knowledge federation catalog and search tools."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

CORE_PATH = Path(__file__).resolve().parents[1] / "federation-core"
sys.path.insert(0, str(CORE_PATH))

from federation_core import get_catalog as read_catalog
from federation_core import grep as federation_core_grep
from federation_core import search as federation_core_search
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("knowledge-federation")


@mcp.tool()
def get_catalog() -> str:
    """Return the authoritative CATALOG.md text without copying it."""
    return read_catalog()


@mcp.tool()
def federation_search(query: str, deep: bool = True) -> dict[str, Any]:
    """Find knowledge by meaning: article hybrid plus live entries by default; deep=false searches entries only. Returns claims and locators, no body snippets. For literal text locations choose federation_grep explicitly."""
    return federation_core_search(query, deep=deep)


@mcp.tool()
def federation_grep(query: str) -> dict[str, Any]:
    """Find literal text locations, ignoring case, in published Catalog files. Independent of semantic search and backends; returns claims and line numbers, no body snippets. Use federation_search for knowledge by meaning."""
    return federation_core_grep(query)


if __name__ == "__main__":
    mcp.run()
