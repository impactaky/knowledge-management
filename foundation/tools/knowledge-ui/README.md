# Knowledge browser and indexer

FastAPI browser, Markdown renderer, Catalog indexer and read-only checker.
Use the shared [setup guide](../../../docs/setup.md). Python dependencies are
locked here; Deno builds pinned browser assets without a runtime CDN dependency.
The browser may display drafts and learning maps; this does not search-publish them.

Search responses include complete current CONTEXT glossary definitions alongside
their locators. Definition size limits and whole-field omission follow the
[shared Core contract](../../integrations/federation-core/README.md); article
snippets are a separate UI projection.

## Article comments

Open `/?file=<absolute article path>` (optionally with `#anchor`) to start the
browser on that article with its comment panel; the address bar follows the
selected article, so it can be copied as a link. `/article?file=...` is the bare
rendered page and has no comment panel.

Select text in a Markdown preview, choose **選択箇所にコメント**, enter a comment
and save. The adjacent list loads from disk on each article load, with quotation,
body, creation time and open/resolved status. Its heading button scrolls to the
original section; **解決**, **再開** and **削除** update the sidecar. An article edit
can make a saved heading anchor stale; the quotation remains available.

Catalog Package `.md` articles and the selected Catalog's sibling `drafts/` are
supported. Executable `.py` articles are excluded. An existing article's absolute
path is required; outside paths and symlink escapes are rejected. Comments for
`articles/topic/guide.md` live at `articles/topic/.comments/guide.md`, without a
separate storage setting. Sidecars are intended for Git tracking; the UI performs
no Git operations. They stay out of trees, search indexes and publication.

Each sidecar is UTF-8 Markdown. For example:

```markdown
# Article comments

<!-- comment:0123456789abcdef0123456789abcdef -->
{"id": "0123456789abcdef0123456789abcdef", "created": "2026-01-01T09:00:00+00:00", "status": "open", "heading": "Evidence", "anchor": "evidence"}

### Quote
> Synthetic passage.

### Comment
    Please clarify this example.
<!-- /comment:0123456789abcdef0123456789abcdef -->
```

The stable ID pairs the boundary markers; the JSON metadata line holds creation
time (ISO 8601), status, heading text and anchor. Quoted lines start with `> `;
body lines use four spaces, including blank lines. This keeps arbitrary comment
text from becoming structural markers. Unknown or malformed records and manual
notes remain untouched by mutations; only valid records appear in the UI.
Repair malformed records in the Markdown file if they need to reappear. Writes
use atomic replacement and a POSIX advisory directory lock across UI workers.
External editors must coordinate separately to avoid overwriting UI changes.

API: `GET /api/comments?file=...`, `POST /api/comments` with JSON
`{file, heading, anchor, quote, body}`, `PATCH /api/comments/{id}` with
`{file, status}` (`open` or `resolved`), and `DELETE /api/comments/{id}?file=...`.
Each returns `{comments: [...]}`. To ask an AI to handle feedback, use the MCP
[`federation_comments(path)` tool](../../integrations/federation-mcp/README.md).
