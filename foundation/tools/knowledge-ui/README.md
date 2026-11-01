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
The ◧ and ◨ header buttons collapse the navigation sidebar and the comment
panel; each choice is remembered in the browser.

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

## Review rounds

`article_url.py <absolute.md>` prints the comment-panel URL and claims the
requesting conversation through `KNOWLEDGE_REVIEW_NOTIFIER` when configured.
Select **修正して**, **承認**, or **差戻し** (with a target round), then **送信**.
Open comments and the current article are captured once; new comments remain
possible while waiting, but the next submission requires a reply. Approval ends
review without a reply and requires a fresh claim before another submission.
Approval does not authorize Stock: confirm that decision in conversation.

The panel shows the recipient title/status and delivery result. Working owners
may take 30 minutes; blocked owners are shown as waiting for approval. On missing,
unconfigured or failed delivery, copy the supplied line into the conversation.
Notifications contain only path, round and verdict, never comment text.
Agent summaries appear above the article; **?** opens a question and answer field.
Answers are comments with optional `reply_to` metadata (older sidecars still
work). Partial/skipped reasons appear on comments; done readings stay in the
record. **差分** compares the latest submitted Markdown with the current file,
using green additions and red struck deletions (Japanese characters, Latin words).
Unsent comments, quotations and answers survive reload in localStorage when
browser storage is available.

Records are local, untracked `.reviews/<slug>/` beside the article:

```text
owner.json                      # opaque notifier destination + at
rounds/<n>/submission.json       # verdict, revertTo?, comment_ids, base_sha, at, owner_at
rounds/<n>/base.md               # submitted article snapshot
rounds/<n>/delivery.json         # status, title?, at
rounds/<n>/reply.json            # round, summary?, general?, orders
```

Ignore `.reviews/` in store Git configuration. These files are excluded from
trees, search indexes, publication and federation reads. Move `.reviews/<slug>/`
together with the article when stocking it. Writes use the same directory lock
as comments and atomic replacement; external editors need separate coordination.
Delivery is an in-process background task; a server interruption can leave it
waiting. Inspect the record before manually forwarding the notification.

Run CLI commands with `uv run --project /path/to/knowledge-ui python
/path/to/knowledge-ui/review.py <command>` and the explicitly selected Catalog:

- `claim <path>`: record the calling conversation; unknown identity is a harmless
  stderr notice and exit 0.
- `show <path> [--round n]`: read verdict, comments, previous questions/answers.
- `skeleton <path> <n>`: emit reply JSON with one order per submitted comment.
- `reply <path> <n> <reply.json>`: validate exact IDs and one-line readings, save
  and claim ownership. `done` resolves the comment; `partial`/`skipped` requires
  `reason`; `needsConfirm: true` requires `question`. Optional `general` accepts
  `done|partial|skipped|noted`; optional `summary` is the human-facing sentence.
- `revert <path> <n>`: mechanically restore `revertTo`'s base and reply with every
  order skipped for reason `revert`. Approval does not accept `reply`.

API: `GET /api/reviews?file=`, `POST /api/reviews/submit` with
`{file, verdict, revertTo?}`, `GET /api/reviews/diff?file=&round=`. Notification runs
in the background after the submission response. MCP remains read-only.

## Notifier and authentication

`KNOWLEDGE_REVIEW_NOTIFIER` is an absolute executable path. Protocol:

- `whoami`: destination JSON on stdout; exit 2 if identity is unknown.
- `status <owner.json>`: JSON `{status, title?}`, status
  `idle|working|blocked|done|absent`.
- `deliver <owner.json> <message>`: JSON `{status, title?}`; exit 0 delivered,
  2 absent, 3 blocked, 4 timeout, 1 other failure. Working recipients wait for idle.

Core never interprets destination fields. See the optional
[Herdr adapter](../../integrations/herdr-review-notify/README.md); no integration
is required for manual forwarding.

Loopback binding needs no credentials. Any other `UI_HOST` requires a valid
`KNOWLEDGE_UI_BASIC_AUTH_FILE` or startup fails. Create it using
`uv run --project /path/to/knowledge-ui python /path/to/knowledge-ui/auth.py
/path/to/credentials reviewer`. The command prompts for a password twice and
creates a new mode-0600 file containing `user:pbkdf2-sha256$iterations$saltHex$digestHex`.
Keep it outside Git. All pages, assets and APIs require Basic authentication;
comparison is constant-time. Use trusted encrypted transport beyond loopback.
