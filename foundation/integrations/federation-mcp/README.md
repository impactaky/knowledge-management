# MCP stdio adapter

Run with the shared uv project; it provides `get_catalog`, `federation_search`,
`federation_grep` and `federation_read`. See [setup](../../../docs/setup.md) for configuration and
[the core contract](../federation-core/README.md) for response semantics.
No Catalog, store, search service or credentials are inferred from the machine.

Search CONTEXT glossary candidates include the complete current authored
`definition`, including applicability and cautions. Oversized definitions are
omitted whole with an explicit reason, retaining the locator when possible.
Article body snippets remain absent; explicit grep returns locations only.

`federation_read(path)` takes an absolute server-side path and returns
`{"path": "...", "content": "..."}`. It applies the same Catalog Package
`PublicationScope` and publication boundary as grep. Excluded directories
(including drafts, tests, worklogs, assets and dot directories), paths outside
Packages, symlink escapes, missing files and directories are rejected. Files
must be UTF-8 text, without NUL bytes, at most 2,000,000 bytes; binary extensions
are also rejected. Invalid reads return MCP tool errors.

The [Hermes provider](../hermes-federation/README.md) can use this adapter through
`memory.federation.mcp_command`, including an SSH stdio command to a synthetic
host such as `knowledge.example.invalid`. This retrieves the Catalog for prompt
injection and forwards all three federation tools without local knowledge files.
