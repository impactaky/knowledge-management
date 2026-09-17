# Installation and configuration

## Local environment

Supported baseline: Linux, Python 3.12+, uv, Deno 2. The index writer uses POSIX
`fcntl` locks. Python dependencies live in
`foundation/tools/knowledge-ui/.venv`; `uv.lock` pins the tested environment.
Deno's exact browser dependencies and integrity hashes are in `deno.json` and
`deno.lock`. No machine-wide Python packages, npm install, or runtime CDN is used.
Place any additional service executables under ignored `.tools/` if not already
available. uv and Deno are prerequisites, not installed by this checkout.

Run the README setup commands at the checkout root. First installation needs
package-registry access. Subsequent `uv run --locked` uses the local environment.
Browser assets must be built once; normal UI startup does not download them.
The build preserves third-party notices beside generated assets.

## Select a store

`FEDERATION_CATALOG` takes precedence over `KNOWLEDGE_CATALOG`. Core CLI,
checker and indexer also accept `--catalog`; that explicit argument wins.
No implicit Catalog exists. Missing selection raises an error before UI startup.
Skills do not supply a fixed Catalog path. For work in this repository, its
`AGENTS.md` selects the root `CATALOG.md` unless the user chooses another store;
configure the tools explicitly with that path. The sample Catalog remains a
separate selection for demonstrations and tests.
Set an absolute Catalog path when launching from an MCP client or another directory.
The UI resolves it once at startup, while reading package contents and entrances
from that Catalog during requests. Restart to change the selected Catalog.

A Catalog section is `## Packages` or `## パッケージ`, with entries like:

```markdown
- [rules](rules/tooling.md) — Read when editing measurement scripts.
- [articles](articles/) — Reviewed explanations.
```

Paths resolve against the Catalog directory, not the shell working directory.
File entrances select the containing directory as package scope; they are not
single-file access restrictions. Directory entrances need no INDEX. Names and
resolved roots must be unique. Nested roots use the most specific owner. Use a
trailing slash for a directory, including one not yet created. Local absolute
paths and `file:` URLs are accepted; other URL schemes are rejected. An explicit
external package is a deliberate read authorization, not an implicit store.
Symlink targets cannot escape the package or reopen excluded work directories.

To make your own store, copy `examples/minimal/` outside this tooling checkout,
replace its synthetic files and select its Catalog. Initialize its Git history
separately if you want article commits. The sample's deliberately tracked draft
and worklog are fixtures; new drafts and worklogs are ignored by its `.gitignore`.

## Configuration reference

| Variable | Behavior |
|---|---|
| `FEDERATION_CATALOG`, `KNOWLEDGE_CATALOG` | Required Catalog selection, in that precedence order |
| `MEILI_URL` | Optional explicit Meilisearch destination; no default and no automatic launch |
| `MEILI_API_KEY` | Optional bearer key, used by Core, UI and indexer; keep outside Git |
| `OLLAMA_EMBED_URL` | Optional embedding API URL **as reached by Meilisearch**; configures its `default` embedder during indexing |
| `OLLAMA_EMBED_MODEL` | Model name, default `bge-m3` when an embedding URL is set |
| `KNOWLEDGE_AUTO_INDEX` | `1` enables startup indexing and file watching; default off; requires `MEILI_URL` |
| `KNOWLEDGE_WATCH_INTERVAL` | Watch interval in seconds, default 60 |
| `UI_HOST`, `UI_PORT` | Browser bind address/port, default `127.0.0.1:7776` |
| `KNOWLEDGE_UI_BASE_URL` | Base URL used by the article URL helper |
| `UV_CACHE_DIR`, `DENO_DIR` | Set to checkout `.cache/uv` and `.cache/deno` for local caches |
| `KNOWLEDGE_DATA_DIR` | Optional live-notebook logs; default checkout `.data` |
| `TMPDIR` | Optional location for machine-local indexing lock files and temporary work |

Source Markdown/Python are originals. Index entries, chunks, caches and generated
browser assets are disposable derived state. Refresh operates on `entries` and
`chunks` in the selected backend; use a dedicated service for this Catalog.
`indexer --index-prefix` is for isolated experiments; UI/Core read the unprefixed
indexes, so do not set it for the browser's normal destination.

## Core and MCP

Core is an importable source package and a small CLI. From the checkout root:

```bash
export PYTHONPATH="$PWD/foundation/integrations/federation-core"
uv run --locked --project foundation/tools/knowledge-ui python -m federation_core catalog
uv run --locked --project foundation/tools/knowledge-ui python -m federation_core search 'window mean' --shallow
uv run --locked --project foundation/tools/knowledge-ui python -m federation_core grep 'UNIT_REQUIRED'
uv run --locked --project foundation/tools/knowledge-ui python foundation/integrations/federation-mcp/server.py
```

The last command waits for MCP stdio messages. Configure your client's MCP server
with the following structure, substituting your own absolute checkout and Catalog
paths. `--directory` makes script resolution independent of the client's cwd.
Do not send UI logs to the MCP process's stdout.

```json
{
  "mcpServers": {
    "knowledge-federation": {
      "command": "uv",
      "args": ["run", "--locked", "--directory", "/path/to/checkout", "--project", "foundation/tools/knowledge-ui", "python", "foundation/integrations/federation-mcp/server.py"],
      "env": {
        "FEDERATION_CATALOG": "/path/to/store/CATALOG.md",
        "UV_CACHE_DIR": "/path/to/checkout/.cache/uv"
      }
    }
  }
}
```

Tools are `get_catalog()`, `federation_search(query, deep=true)` and
`federation_grep(query)`. Deep search without a backend reports `unavailable` while
retaining live entries; shallow search reports `not_requested`. Backend failure
never silently invokes grep. A reachable backend with a failing embedder reports
`degraded` and tries lexical article search. These are different from successful
searches with no matches. The agent receives claims and locators, not body prose.

## Optional backend

A dedicated Meilisearch executable is required only for indexed article search.
The real-backend smoke was verified with Meilisearch **1.48.3**. Obtain a binary
for your OS/architecture from the upstream distribution and place it at
`.tools/meilisearch`; it is not vendored or installed machine-wide here.
Start it in a separate terminal from this checkout:

```bash
mkdir -p .data/meili .data/dumps .data/snapshots
.tools/meilisearch --no-analytics --env development --http-addr 127.0.0.1:17700 \
  --db-path "$PWD/.data/meili" --dump-dir "$PWD/.data/dumps" --snapshot-dir "$PWD/.data/snapshots"
```

Then, with the sample Catalog still selected:

```bash
export MEILI_URL=http://127.0.0.1:17700
uv run --locked --project foundation/tools/knowledge-ui python foundation/tools/knowledge-ui/indexer.py
bash foundation/tools/knowledge-ui/run.sh
```

This creates lexical indexes without an embedder. Deep search consequently reports
`degraded`, with lexical article candidates. For semantic search, separately
supply Ollama with its `/api/embed` API and download the chosen model. A local
example, with an upstream Ollama executable placed at `.tools/ollama`:

```bash
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_MODELS="$PWD/.data/ollama"
.tools/ollama serve
# In a second terminal with the same OLLAMA_HOST and OLLAMA_MODELS:
.tools/ollama pull bge-m3
export OLLAMA_EMBED_URL=http://127.0.0.1:11435/api/embed
export OLLAMA_EMBED_MODEL=bge-m3
uv run --locked --project foundation/tools/knowledge-ui python foundation/tools/knowledge-ui/indexer.py
```

Model download needs network access, disk and adequate memory/compute. The model
and runtime have their own licenses. Ollama execution and semantic relevance were
not validated in this extraction; the semantic-success transport test uses a
synthetic HTTP responder. Embedding failures fail the indexing command clearly.
Omitting `OLLAMA_EMBED_URL` leaves existing embedder settings unchanged; use a new
dedicated database for the lexical-only example.

Automatic refresh is opt-in (`KNOWLEDGE_AUTO_INDEX=1`). Manual UI refresh returns
an acceptance status from `POST /api/reindex`; poll `GET /api/reindex` until
`succeeded` or `failed`. CLI refresh waits for tasks and exits nonzero on failure.
It reconciles deleted documents and checks for source changes during indexing.

For a real disposable service test with automatic cleanup:

```bash
uv run --locked --project foundation/tools/knowledge-ui python scripts/smoke_backend.py --binary "$PWD/.tools/meilisearch"
```

It chooses a temporary loopback port/database, indexes synthetic data, withdraws
an article, verifies deletion and stops the process before checking outage behavior.

## Optional executable articles and integrations

Markdown needs no notebook runtime. To author or export trusted marimo notebooks:

```bash
uv sync --locked --project foundation/tools/knowledge-ui --extra notebook
uv run --locked --project foundation/tools/knowledge-ui --extra notebook marimo edit /path/to/article.py
uv run --locked --project foundation/tools/knowledge-ui --extra notebook marimo export html --sandbox --no-include-code /path/to/article.py -o /path/to/__marimo__/article.html
```

The UI serves frozen HTML without executing Python. For the explicit live action,
use the explicit uv command below to retain the optional dependency:

```bash
uv run --locked --project foundation/tools/knowledge-ui --extra notebook uvicorn server:app --app-dir foundation/tools/knowledge-ui --host 127.0.0.1 --port 7776
```

Export `KNOWLEDGE_ENABLE_LIVE_MARIMO=1` before that command if desired.
`KNOWLEDGE_MARIMO_HOST`, `KNOWLEDGE_MARIMO_PORT_START`/`END` and
`KNOWLEDGE_MARIMO_IDLE_SECONDS` default to loopback (or `UI_HOST`), 7780–7879 and
3600. Notebook sandbox dependencies declared in PEP 723 are resolved by uv and may
require network access. Live execution is disabled by default. Serve only trusted
stores on loopback: this browser has no authentication and intentionally allows
draft previews and explicit indexing. Frozen HTML is trusted active content.

Hermes is optional. Deployment-specific orchestration and workflows remain
outside this repository. Hermes may consume the generic MCP
adapter if its separately installed runtime supports an MCP client; its native
memory-provider plugin and config writers are intentionally omitted. There is no
undeclared Hermes Python dependency in Core or MCP.
