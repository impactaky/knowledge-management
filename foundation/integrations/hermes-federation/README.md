# Hermes Federation Provider

Stateless Hermes memory provider for the knowledge federation.

Install and configure with Hermes:

```bash
mkdir -p ~/.hermes/plugins
ln -sfn "/path/to/knowledge-management/foundation/integrations/hermes-federation" ~/.hermes/plugins/federation
hermes config set memory.provider federation
hermes config set memory.federation.catalog_path "/path/to/store/CATALOG.md"
hermes config set memory.federation.meili_url "http://127.0.0.1:7700"  # optional; enables article semantic search
```

The provider reads `memory.federation.catalog_path` and optional `memory.federation.meili_url` from `~/.hermes/config.yaml` (or falls back to `MEILI_URL` in the environment, or accepts an explicit constructor override).
It does not copy or cache knowledge: `system_prompt_block()` reads `CATALOG.md` for each prompt build; `federation_search(query, deep=true)` and `federation_grep(query)` derive package roots from that same catalog on each call.

The default search uses full-text hybrid, returning up to five article locators and current Package INDEX/CONTEXT and article-owned claim entries. `deep=false` keeps the lightweight entry search. `fulltext_results` remains an always-empty compatibility field. Semantic failures retain lexical article search and live entries; Meilisearch outages retain live entries only. Neither case runs full-file grep.

`federation_grep(query)` independently locates literal text, ignoring case, without Meilisearch/Ollama. It returns up to 10 files with three matching line numbers per file in the shared six-field grep response.

Hooks that would create automatic recall or writes are intentionally no-op: `prefetch`, `sync_turn`, and `on_memory_write`. Hermes built-in `MEMORY.md` / `USER.md` behavior can remain enabled alongside this external provider.

For a host without local knowledge files, replace `catalog_path` with a stdio MCP
command array in `~/.hermes/config.yaml`:

```yaml
memory:
  provider: federation
  federation:
    mcp_command:
      - ssh
      - knowledge.example.invalid
      - env
      - FEDERATION_CATALOG=/srv/example-store/CATALOG.md
      - /srv/knowledge-management/foundation/tools/knowledge-ui/.venv/bin/python
      - /srv/knowledge-management/foundation/integrations/federation-mcp/server.py
```

`mcp_command` accepts any stdio MCP argv, without a local shell. Set either
`catalog_path` or `mcp_command`; configuring both is an error. `meili_url` is unused
in remote mode: the server uses its own search configuration.

Each prompt build retrieves `get_catalog` and injects the same
`<federation-catalog>` block with `Source: remote MCP`. Hermes caches its system
prompt per session and rebuilds it at session start and compression. Each tool
call launches a fresh subprocess, initializes MCP, calls the tool and closes the
process; no persistent connection or knowledge cache is kept. The remote client
uses only Python's standard library and has a 30-second exchange timeout. Launch,
protocol and tool failures produce an unavailable Catalog block or a JSON
`{"error": ...}` tool response.

Remote mode exposes `federation_search`, `federation_grep` and
`federation_read(path)`. Use absolute remote locators returned by search/grep to
read UTF-8 file bodies. Reads are limited to the Catalog's published Package
scope and 2,000,000 bytes; excluded work directories, symlink escapes and binary
files are rejected. Local mode retains its two tools because Hermes can read
local files directly. Automatic recall and write hooks remain no-op in both modes.
