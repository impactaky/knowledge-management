# Knowledge browser and indexer

FastAPI browser, Markdown renderer, Catalog indexer and read-only checker.
Use the shared [setup guide](../../../docs/setup.md). Python dependencies are
locked here; Deno builds pinned browser assets without a runtime CDN dependency.
The browser may display drafts and learning maps; this does not search-publish them.

Search responses include complete current CONTEXT glossary definitions alongside
their locators. Definition size limits and whole-field omission follow the
[shared Core contract](../../integrations/federation-core/README.md); article
snippets are a separate UI projection.
