# Development instructions

- This repository contains reusable knowledge-management mechanisms, synthetic examples and articles intended for this repository. Keep actual personal or organizational knowledge in a separate store.
- Never copy a private store wholesale, including its Git history, catalog, logs, drafts, caches, search indexes, or configuration.
- Inspect every imported source file, test fixture, document, and asset. Remove private references and replace machine-specific assumptions with explicit configuration or portable paths.
- Do not follow symlinks into a private store when copying or packaging files.
- Tests must use synthetic fixtures and must not depend on the developer's active catalog or running services.
- Keep examples self-contained. Do not introduce links or defaults pointing to a developer's private filesystem, network, or repositories.
- Before publishing, review the complete tracked file set and Git history. A string scan is useful evidence, not proof that all private information has been removed.

## Catalog selection for this repository

- Unless the user selects a different store, read the root `CATALOG.md` for work here. Use `drafts/` for article candidates and `articles/<theme>/` for approved articles.
- `examples/minimal/CATALOG.md` belongs to the synthetic example, not this repository's article store.
- Configure Core, UI and the MCP server with this root Catalog explicitly. They do not discover it from the working directory; a connected MCP server may be configured for another store.
- Do not treat another server's returned Catalog as permission to write to that store. Never guess its filesystem root from its contents or neighboring directories. Resolve package paths from the selected Catalog's verified location.
- Present the full proposed article destination before stock approval.
