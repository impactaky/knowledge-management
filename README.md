# knowledge-management

Canonical, reusable mechanisms for creating, reviewing, adopting, searching and browsing
knowledge across knowledge stores. This repository is the authoritative source for the
shared core, MCP adapter, UI, indexer, checker, Hermes plugins, workflows, and vocabulary.
Users and organizations manage their actual knowledge in separate stores configured by
selecting their own Catalog.

Core, MCP, the browser, indexer, checker, Hermes plugins, and eight knowledge workflows are
implemented here. The bundled store contains only synthetic examples.
**Repository creation has been authorized by the author; project license remains undecided.**
No project-wide license is granted by this repository; see [provenance and notices](docs/provenance.md).

## Start with the sample

From this checkout, with uv and Deno 2 available:

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
export DENO_DIR="$PWD/.cache/deno"
export FEDERATION_CATALOG="$PWD/examples/minimal/CATALOG.md"
unset KNOWLEDGE_CATALOG MEILI_URL MEILI_API_KEY OLLAMA_EMBED_URL KNOWLEDGE_AUTO_INDEX
uv sync --locked --project foundation/tools/knowledge-ui
deno task --config foundation/tools/knowledge-ui/deno.json build-browser-assets
uv run --locked --project foundation/tools/knowledge-ui python foundation/tools/knowledge-ui/check.py
uv run --locked --project foundation/tools/knowledge-ui python foundation/tools/knowledge-ui/indexer.py --dry-run
bash foundation/tools/knowledge-ui/run.sh
```

Open <http://127.0.0.1:7776>. Expand `sample-articles` → `measurement` and open
“Computing a window mean”. Search `window mean` for an authored claim, or use
**Text search** with `UNIT_REQUIRED` for a literal rule location. No backend is
needed for browsing, live claim/entrance search, explicit grep, or checking.

- [Installation, configuration, MCP and optional search services](docs/setup.md)
- [Understanding → draft → human review → stock → search → browse](docs/workflow.md)
- [Search and publication contract](foundation/integrations/federation-core/README.md)
- [Synthetic Catalog](examples/minimal/CATALOG.md)
- [Extraction scope and acceptance evidence](docs/extraction.md)

## Work with this repository's articles

The root [CATALOG.md](CATALOG.md) selects this repository's mechanisms and
`articles/`. It is distinct from the synthetic sample. Draft new articles in
`drafts/`, then stock reviewed articles under `articles/<theme>/`. Keep private
personal and organizational knowledge in a separately selected store.

To select this Catalog for commands launched from the checkout root:

```bash
export FEDERATION_CATALOG="$PWD/CATALOG.md"
```

Set the same absolute path in the MCP server's environment and restart the server
to switch its store. Creating a local Catalog or exporting a variable in a shell
does not reconfigure an already connected MCP server. See [store selection](docs/setup.md#select-a-store).

## Verify

```bash
uv run --locked --project foundation/tools/knowledge-ui pytest foundation/tests -q
uv run --locked --project foundation/tools/knowledge-ui python scripts/smoke.py
```

The tests use temporary synthetic stores and mock backends; the smoke script
starts a temporary loopback UI and MCP subprocess. Neither uses existing search
services. See the setup guide for the optional disposable real-backend smoke.
