# MCP stdio adapter

Run with the shared uv project; it provides `get_catalog`, `federation_search`,
`federation_grep`, `federation_read` and `federation_comments`. See [setup](../../../docs/setup.md) for configuration and
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

## Explicit comment reads

`federation_comments(path)` accepts an existing article's absolute `.md` path
and returns its raw UTF-8 comment Markdown:
`{"path": ".../.comments/guide.md", "exists": true, "content": "..."}`.
If no sidecar exists it returns `exists: false` and an empty `content`.
Invalid paths produce MCP tool errors. Catalog Package roots and the selected
Catalog's sibling `drafts/` are allowed; relative paths, `.py` articles, outside
paths and symlink escapes are rejected. Comment directory/file symlinks are
rejected too. No search backend is required.

The [UI README](../../tools/knowledge-ui/README.md#article-comments) documents
the Markdown format with a complete example. Each record has a stable ID,
ISO 8601 creation time, open/resolved status, heading text/anchor, quotation and
body. For example, an open comment on the heading `Evidence` can quote
`Synthetic passage.` and request `Please clarify this example.` Agents receive
the complete sidecar, including malformed records and manual notes, so they can
review feedback explicitly. This tool is read-only and does not submit comments
to an AI automatically. `federation_read` still rejects `.comments/` and drafts;
comments are not search-published. The Hermes provider does not forward this new
tool.
