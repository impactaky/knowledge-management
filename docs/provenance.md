# Provenance and release status

## Project status

This repository is the authoritative canonical implementation for reusable knowledge-management mechanisms (search Core, MCP adapter, UI, indexer, checker, eight shared workflows, and shared vocabulary/rules). Creation and maintenance of this public repository has been authorized by the owner. Private stores (such as personal or organizational knowledge repositories) consume this common core directly without maintaining duplicate implementations. No source Git history, private Catalog, articles, profiles, service files, credentials or regression corpus is imported.
The project-wide license remains **undecided**. Third-party licenses below apply only to their respective components.

## Selected implementation

| Destination | Origin and treatment |
|---|---|
| `foundation/integrations/federation-core/federation_core/` | Canonical Core modules; portable explicit configuration and Catalog syntax; CLI included |
| `foundation/integrations/federation-mcp/server.py` | Canonical MCP adapter, using the shared project environment |
| `foundation/integrations/hermes-federation/` | Canonical Hermes memory provider for deterministic CATALOG prompt injection and read-only search/grep |
| `foundation/tools/knowledge-ui/` | Canonical renderer, frontend, checker and indexer; portable configuration, explicit service/execute opt-ins and offline document generation |
| `skills/` | Eight canonical workflows, selected references and SVG helper; portable behavior without vendor-specific metadata or mandatory private directories |
| `foundation/modules/` | Canonical shared vocabulary for Knowledge Federation and Article Curation |
| `foundation/tests/` | Synthetic test modules using generated fixtures and synthetic stores; no dependency on active catalogs or live backends |
| `examples/minimal/`, public explanations, smoke scripts | Synthetic examples and standalone guidance |

Delivery coordination, agent exchange, private operations, historical ADRs, and private regression corpora remain in their respective private stores.

## Third-party components

Python packages are installed through `pyproject.toml` and `uv.lock`, not copied
into tracked source. Their distribution metadata and license files remain in the
local environment. They include FastAPI, Uvicorn, Meilisearch's Python client,
Markdown-it-py and plugins, Pygments, HTTPX, PyYAML, MCP and optional marimo.
Review the exact locked distributions and transitive dependencies before shipping
a binary, container or environment; this repository does not relicense them.

Browser packages are pinned upstream distributions resolved by Deno:

| Component | Version | Package license |
|---|---|---|
| KaTeX | 0.16.22 | MIT; Khan Academy and contributors |
| Mermaid | 11.12.0 | MIT; Knut Sveidqvist |
| Water.css | 2.1.1 | MIT; Kognise |

The build copies distributions without rewriting their embedded notices, and
copies LICENSE/NOTICE/COPYING files for the locked package set alongside assets.
`static/browser-assets/third-party.json` records names, versions, declared licenses
and notice paths (122 locked package entries in the initial build). Generated
assets and notices are ignored in Git, but retain them together if packaging or
serving that directory. Public npm package metadata and lock hashes are not
private source data. Meilisearch, Ollama and any downloaded embedding model are
separate optional runtimes, not vendored components.

Before public release, select the project license, review all tracked content and
history, and review the notices for the actual distribution format. Extraction
checks and string scans support that review; they do not replace it.
