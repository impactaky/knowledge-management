# Public-use extraction (Historical initial extraction)

> [!NOTE]
> This document records the initial public extraction. For current architecture, setup, and Hermes plugin configuration, see [setup](setup.md), [workflow](workflow.md), and [provenance](provenance.md). The native Hermes memory provider (`hermes-federation`) is maintained directly in this repository as its canonical implementation.

The authorized copy is implemented in this repository. It is not a migration,
source cutover or automatic synchronization. The original store, configuration,
services and harness wiring remain outside this project's operations.

## Included

- Shared Catalog/search/publication/claim modules and a standalone CLI.
- MCP stdio tools for Catalog retrieval, candidates and explicit literal grep.
- Knowledge browser, locally built assets, document indexer and read-only checker.
- Eight knowledge workflows and their required portable references/helpers.
- A synthetic store with file/directory entrances, an adopted article with claims,
  pending draft, worklog and learning map demonstrating search exclusions.
- Explicit Catalog/backend selection, local dependencies/caches, opt-in indexing
  and optional trusted notebook execution.

The parser now accepts both `## Packages` and `## パッケージ`, resolving the
bootstrap example mismatch. Missing Catalog selection fails clearly; missing
backend configuration preserves live entry search and browsing. No live service
or store is a default. INDEX is optional, claims belong to articles, and human
approval precedes moving drafts into the article layer.

Deployment-specific orchestration and workflows remain private store concerns.
The native Hermes memory provider is maintained directly in this repository (see [setup](setup.md)).
No personal knowledge, source history, generated index or private fixture is
part of the selected implementation. Third-party origins and the undecided
project license are recorded in [provenance](provenance.md).

## Acceptance evidence

Reproduce with the commands in the [README](../README.md) and [setup](setup.md).
The suite covers English/Japanese Catalog syntax, optional entrances, nested scope,
symlink escapes, work exclusions, article claims and current-source validation,
query/response bounds, index documents and backend task failures, Markdown/UI
responses, and configured HTTP success/degradation/outage behavior.

The standalone smoke invokes Core CLI and MCP stdio, starts a temporary UI,
fetches an adopted article and four static assets, checks the store, and generates
three entries and three chunks without a backend. The optional real-backend smoke
uses a disposable Meilisearch database and validates lexical fallback, withdrawal,
stale deletion and unavailability after shutdown. No production index is used.

Remaining release/runtime limits: license selection is pending; real Ollama
embedding quality and optional live notebook execution are not validated here.
The tested browser/HTTP contracts do not constitute a full visual browser QA pass.
The dependency environment may report upstream deprecation warnings. See the
private implementation worklog for exact run evidence and source audit hashes;
private work paths and audit search strings are intentionally not published here.
