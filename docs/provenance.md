# Provenance and release status

## Project status

The owner authorized a local, independent extraction from their private knowledge
management implementation. No source Git history, private Catalog, articles,
profiles, service files, credentials or regression corpus was imported. The
original store remains authoritative and is not synchronized by this project.
The project-wide license is **undecided**. Local implementation is authorized;
public release and any project-wide license grant await the owner's decision.
Third-party licenses below apply only to their respective components.

## Selected implementation

| Destination | Origin and treatment |
|---|---|
| `foundation/integrations/federation-core/federation_core/` | Owner-provided Core modules; private defaults removed, configuration and Catalog syntax adapted; CLI added |
| `foundation/integrations/federation-mcp/server.py` | Owner-provided MCP adapter, moved to shared local dependency declaration |
| `foundation/tools/knowledge-ui/` | Owner-provided renderer, frontend, checker and indexer; portable configuration, explicit service/execute opt-ins and offline document generation added |
| `skills/` | Eight owner-provided workflows, selected references, SVG helper and agent metadata; private references and external mandatory skill dependencies removed |
| `foundation/tests/` | Three selected source test modules using generated fixtures, adapted for explicit configuration; new synthetic integration tests |
| `examples/minimal/`, public explanations, smoke scripts | Synthetic examples and standalone guidance written for this extraction |

Source experiment runners/corpora, migration utility, native Hermes plugin,
service units, launcher and delivery workflows were omitted. The HTML-to-Markdown
helper was not required by the selected skills and is not shipped. Upstream
`grilling`, `domain-modeling` and marimo skills were dependency references only;
no third-party skill text, format templates or runtime code is vendored.

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
