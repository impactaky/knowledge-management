# MCP stdio adapter

Run with the shared uv project; it provides `get_catalog`, `federation_search`
and `federation_grep`. See [setup](../../../docs/setup.md) for configuration and
[the core contract](../federation-core/README.md) for response semantics.
No Catalog, store, search service or credentials are inferred from the machine.

Search CONTEXT glossary candidates include the complete current authored
`definition`, including applicability and cautions. Oversized definitions are
omitted whole with an explicit reason, retaining the locator when possible.
Article body snippets remain absent; explicit grep returns locations only.
