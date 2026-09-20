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
