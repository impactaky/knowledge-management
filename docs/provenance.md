# Provenance and release status

## Project status

This repository is the authoritative canonical implementation for reusable knowledge-management mechanisms (search Core, MCP adapter, UI, indexer, checker, shared knowledge and delivery/agent workflows, and shared vocabulary/rules). Creation and maintenance of this public repository has been authorized by the owner. Private stores (such as personal or organizational knowledge repositories) consume this common core directly without maintaining duplicate implementations. No source Git history, private Catalog, articles, profiles, service files, credentials or regression corpus is imported.
The project-wide license remains **undecided**. Third-party licenses below apply only to their respective components.

## Selected implementation

| Destination | Origin and treatment |
|---|---|
| `foundation/integrations/federation-core/federation_core/` | Canonical Core modules; portable explicit configuration and Catalog syntax; CLI included |
| `foundation/integrations/federation-mcp/server.py` | Canonical MCP adapter, using the shared project environment |
| `foundation/integrations/hermes-federation/` | Canonical Hermes memory provider for deterministic CATALOG prompt injection and read-only search/grep |
| `foundation/integrations/hermes-federation-workflows/` | Canonical Hermes workflow discovery plugin with deduplication between shared and store-local workflows |
| `foundation/tools/knowledge-ui/` | Canonical renderer, frontend, checker and indexer; portable configuration, explicit service/execute opt-ins and offline document generation |
| `skills/` | Canonical workflows (knowledge, delivery coordination and agent exchange), selected references and helpers; portable behavior without vendor-specific metadata or mandatory private directories |
| `foundation/modules/` | Canonical shared vocabulary for Knowledge Federation, Article Curation, Delivery Coordination and Agent Exchange |
| `foundation/docs/adr/` | Canonical decisions for this repository's shared mechanisms |
| `foundation/tests/` | Synthetic test modules using generated fixtures and synthetic stores; no dependency on active catalogs or live backends |
| `examples/minimal/`, public explanations, smoke scripts | Synthetic examples and standalone guidance |

Private operations, historical private ADRs, and private regression corpora remain in their respective private stores.

## Shared delivery coordination and agent exchange

The `propose`, `order`, and `agent-exchange` skills and the Delivery
Coordination / Agent Exchange vocabulary were selected from a read-only local
snapshot of a private knowledge store and adapted for the common core on top of
this repository's commit `47a716ca471955cbd5b01cc548accbe2fd4c2226`. The
selected source files correspond to the private source repository at commit
`2c1498a26bcd52b16afe4a9847a84673d0ac0477`. That repository's path and store
name are not recorded here, and its Git history, Catalog, configuration,
deployment state and store name are not imported. No symlink in this repository
points into a private store.

Public-use adaptations:

- Vendor-specific skill metadata was not copied; only `SKILL.md` and portable
  supporting files are included.
- Fixed agent kind, model and reasoning-effort values were removed. The order
  and Agent Exchange Launchers resolve `kind` and ordered native args from an
  explicit path (`ORDER_CONFIG` / `AGENT_EXCHANGE_CONFIG`), then
  `${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/`, and stop before
  starting any agent if no valid selection exists.
- Private deployment paths, shell profiles, user names, and a specific agent
  installation path were removed. The systemd adapter resolves an absolute
  deployment env file at install time and leaves deployment values out of the
  generated units. Only `config.example.toml` is committed.
- The fixed worklog-store branches were removed. `order` uses the absolute
  worklog directory confirmed by the parent session (or `AGENT_WORKLOG_DIR`),
  optionally located from an explicit config root, and stops if it cannot be
  resolved.
- Herdr remains an external system. It is not vendored, and the order workflow
  does not call the file-based Agent Exchange protocol.
- The Agent Exchange protocol boundary is unchanged: same host, same OS user,
  local filesystem, at most one Response per Request, serial Threads, file
  state as the source of truth, fail-closed, and no automatic fallback, queue,
  retry, or close. Shell tests pin these semantics with a temporary exchange
  directory and a fake Herdr.

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
