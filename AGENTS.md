# Development instructions

- This repository contains reusable knowledge-management mechanisms, synthetic examples and articles intended for this repository. Keep actual personal or organizational knowledge in a separate store.
- Never copy a private store wholesale, including its Git history, catalog, logs, drafts, caches, search indexes, or configuration.
- Inspect every imported source file, test fixture, document, and asset. Remove private references and replace machine-specific assumptions with explicit configuration or portable paths.
- Do not follow symlinks into a private store when copying or packaging files.
- Tests must use synthetic fixtures and must not depend on the developer's active catalog or running services.
- Keep examples self-contained. Do not introduce links or defaults pointing to a developer's private filesystem, network, or repositories.
- Before publishing, review the complete tracked file set and Git history. A string scan is useful evidence, not proof that all private information has been removed.
- When changing `README.md` or `README.ja.md`, update the other in the same change so their content matches.

## Portable skills

- Design skills to work across agent environments. Keep their instructions in `SKILL.md` and portable supporting files; do not include `agents/openai.yaml` or other vendor-specific skill metadata.
- Describe required capabilities instead of requiring a particular agent product, invocation syntax, tool name, installation directory, or UI. Keep environment-specific registration and adapters in setup documentation or integrations.
- Resolve resources relative to the skill's verified location and knowledge paths from the explicitly selected Catalog. Do not assume the current working directory or a machine-specific path.
- Document dependencies and alternatives for optional capabilities. If a required capability is unavailable, report the missing capability and continue only the work that does not depend on it; preserve review and approval boundaries.

## Catalog selection for this repository

- Unless the user selects a different store, read the root `CATALOG.md` for work here. Use `drafts/` for article candidates and `articles/<theme>/` for approved articles.
- `examples/minimal/CATALOG.md` belongs to the synthetic example, not this repository's article store.
- Configure Core, UI and the MCP server with this root Catalog explicitly. They do not discover it from the working directory; a connected MCP server may be configured for another store.
- Do not treat another server's returned Catalog as permission to write to that store. Never guess its filesystem root from its contents or neighboring directories. Resolve package paths from the selected Catalog's verified location.
- Present the full proposed article destination before stock approval.
