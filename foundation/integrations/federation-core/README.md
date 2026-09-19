# Federation Core

Dependency-light search shared by MCP, Hermes, and knowledge-ui. Package scope
is derived from the authoritative `CATALOG.md` on every call; the core keeps no
package registry or knowledge copy.

Catalog targets may be files or directories. `Package.entry_path` is the actual
target; `root` is its parent for a file, or the directory itself. Use a trailing
slash for directory targets (including missing directories). No INDEX is
implied or required. Duplicate names/roots are errors; the checker validates
entrance existence separately from package contents. Retained INDEX/CONTEXT
files remain searchable. A direct rule-file entrance is available through
explicit `federation_grep` and direct reading, without adding rule bodies to Agent results
or to article embeddings.

`federation_search(query, deep=true)` always returns only these top-level
fields:

```json
{
  "query": "...",
  "deep": true,
  "semantic_status": "available",
  "index_results": [],
  "article_results": [],
  "fulltext_results": [],
  "warnings": [],
  "truncated": false,
  "limits": {"candidate_bytes": 2048, "response_bytes": 16384, "query_bytes": 512,
             "encoding": "UTF-8 JSON; ensure_ascii=false; default separators or indent=2"},
  "omitted": {"candidates": 0, "fields": 0, "query": false}
}
```

- `deep=false` searches Package `INDEX.md` / `CONTEXT.md` and article-owned claims, and sets
  `semantic_status` to `not_requested`.
- The default `deep=true` also asks Meilisearch for relevant article locators.
  Article hits below the core's calibrated score gate are omitted; no match is
  a successful `available` result.
- If semantic search fails after Meilisearch is reached, status is `degraded`
  and lexical article search plus live entries remain. If Meilisearch cannot
  be reached, status is `unavailable` and only live INDEX/CONTEXT/article-claim
  entries remain. Neither state runs full-file grep.
- `fulltext_results` is an always-empty compatibility field. Search never
  populates it, including on backend failures.

`federation_grep(query)` (Python: `grep(query, catalog_path=...)`) is an independent
operation for locating a literal phrase, using case-insensitive substring matching
of the whole trimmed query. It uses no Meilisearch or Ollama and never switches to
search on failure or no match. Its response has exactly six fields:

```json
{
  "query": "...",
  "fulltext_results": [],
  "warnings": [],
  "truncated": false,
  "limits": {"candidate_bytes": 2048, "response_bytes": 16384, "query_bytes": 512,
             "encoding": "UTF-8 JSON; ensure_ascii=false; default separators or indent=2"},
  "omitted": {"candidates": 0, "fields": 0, "query": false}
}
```

Choose search for knowledge candidates by meaning, or grep explicitly for text
locations; providers never automatically run both. Grep scans the same published
Catalog scope and returns at most 10 files with three matching source line numbers
each. It does not suppress INDEX, authored article-claim or body locations already
represented by entries or hybrid search. Existing file exclusions and symlink
checks remain; no new asset/JavaScript exclusion is introduced.

The populated result fields in both APIs return locators: `package`, absolute `path`, current
`title`, `section`/`anchor` when available, and source `line` where applicable.
Article rows also have the engine `score`; it is diagnostic, **not** a common
ranking scale. Preserve the engine's first published hit per article in engine
order. Full-text rows carry at most three `match_lines`, never matching prose.
No path returns `text`, `body`, or `snippet` to an Agent. Search `index_results`
add `definition` for authored CONTEXT glossary terms, retaining `kind="context"`.
It contains the complete current Markdown block starting at `**Term**:` (including
an inline definition, subsequent paragraphs and applicability/caution text such
as `_Avoid_`), up to the next term or Markdown section heading. Fenced examples
cannot define terms. Term-name and definition-paragraph hits share the term's
source line, section and anchor and are deduplicated. Non-glossary CONTEXT hits
may remain locator-only. Explicit grep never adds glossary definitions.

When authored, `claim` is one complete current article-frontmatter string
(or a Package INDEX bullet). `claim_source` gives the real source path and
line; `links` gives published targets. Prefer a self-anchor for the selected
section, otherwise the first claim in array order. A lexical article-claim
entry selects its matched claim. No `claims` array or body is returned.
When neither a claim nor a glossary definition is authored, `omitted` includes
`claim:not_authored`; malformed metadata also adds `claim:invalid`.
Publication is independent of authoring completeness.

Limits are 10 index rows, 5 unique articles and 10 text-match files. The engine
candidate window remains 50; exclusions/duplicates may underfill five and
engine overflow sets `truncated`. There is no paging. Each candidate is at most
2,048 UTF-8 JSON bytes and the whole core response at most 16,384 (Python
`json.dumps(..., ensure_ascii=False)` with either default separators or
`indent=2`, as used by FastMCP TextContent). Oversized
metadata is omitted whole with a reason; oversized paths cause whole-candidate
omission. Counts appear in top-level `omitted`; `truncated` signals loss.
Definitions are never sliced: `definition:byte_limit` marks candidate overflow,
and `definition:response_byte_limit` marks response overflow. Definitions are
removed before dropping locators to fit the response, preserving article selection
when the added glossary payload would otherwise displace it.
Both APIs reject empty queries. Queries over 512 UTF-8 bytes are omitted and
explicitly not executed; only search has `semantic_status=not_requested`. Providers serialize Unicode without ASCII
escaping; transport framing and UI-added snippets are outside this JSON budget.

Cached Package INDEX and article-claim entries must still exist exactly in the current source. Moved claims
are relocated, edited/deleted ones invalidate the hit. Articles resolve their
Claim Line from current article frontmatter on every search. Titles use the current
first H1 (or filename stem); executable articles use static `mo.md` prose.
Current headings replace cached metadata; missing sections are omitted with
`section:stale_index`. No cached claim/title/section is trusted as current.
This guarantees source selection metadata, not embedding freshness: an old
vector can still retrieve an irrelevant surviving article until index refresh.
CONTEXT term blocks are resolved by their authored term name in the current file,
so edited definitions (including inline ones) are current and moved terms get their
new source line. Deleted, renamed, fenced or unpublished terms cannot supply an
old definition. This applies to Meilisearch entries and live entry hits in both
deep modes, including backend outages; cached definition text and ranges are not
trusted as current.
Concurrent edits within one search are not an atomic multi-file snapshot.

## Search publication

The catalog defines package scope, not publication of every file under a root.
`federation_core/publication.py` supplies the same publication rules to live
search and grep, the knowledge-ui indexer, and suggestions:

- Exclude `drafts/`, `tests/`, `test/`, `fixtures/`, hidden directories,
  `worklogs/`, `experiments/`, `evaluations/`, `books/`, `studies/`, the existing
  `regression-queries.json`, caches, dependencies, and generated `__marimo__/` views relative to the Catalog directory for local packages, or the outermost
  containing Catalog package for external roots. Work-directory roots are also excluded. A nested package cannot reopen an excluded directory. An
  explicit isolated fixture Catalog with no enclosing package still works.
- Article bodies are `.md` / static marimo `.py` files immediately inside
  `articles/<theme>/`. Catalog roots can point to the repository, article layer,
  or a theme. Names are labels, not type selectors; nested roots use the most
  specific owner. Assets, generated views, INDEX/CONTEXT and work maps are not
  article bodies. The Package INDEX and CONTEXT remain searchable entrances.
- Discover articles without following theme INDEX links. Moving an article to
  `drafts/` immediately withdraws cached hits; removing claims only creates an
  authoring error. New articles require no INDEX update or publication flag.
- Claims live in the opening YAML frontmatter as a non-empty array of non-empty
  strings. Block/flow arrays and quoted/block scalars are supported; duplicate
  keys, aliases and non-string items are rejected. Existing metadata is kept.
  For marimo only the first static `mo.md` literal can own frontmatter. No
  Python is executed. `articles.py` supplies the shared parser and locations.
- `article-claim` entries use the article path and actual source line, including
  Python literals with Unicode, indentation or escaped newlines. Body chunks
  and human snippets exclude frontmatter. Normal refresh removes retired
  INDEX entries; live validation excludes stale entries before refresh too.

Stock requires the author's content approval, then moves the article with its
claims from draft to the theme. Browsing, previews and learning maps remain
independent of search publication. No theme claim inventory is generated.

The shared module requires PyYAML >= 6.0. knowledge-ui declares it in its
`pyproject.toml`, MCP uses that same repository-local environment. Use it when invoking core helpers.

## Portable configuration

No Catalog or backend is selected by default. Set `FEDERATION_CATALOG` (preferred)
or `KNOWLEDGE_CATALOG`. Missing selection raises a clear error. The package
section accepts `## Packages` and `## パッケージ`; relative paths resolve against
the Catalog directory. Use a trailing slash for directory entrances.

`MEILI_URL` explicitly enables backend access. Without it, live entries work
and deep search reports unavailable; explicit grep never contacts a backend.
`MEILI_API_KEY` optionally supplies authentication. See [setup](../../../docs/setup.md).
The inherited score gate is a heuristic; this copy makes no new relevance claim.
