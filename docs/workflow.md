# From understanding to reusable knowledge

Keep private knowledge stores separate from this tooling checkout. For articles
intended for this repository, select its root `CATALOG.md`; the synthetic example
has its own Catalog. Respect the selected Catalog's reading instructions and trust boundaries. Search publication
means availability through local federation search; it is not Internet publication.

1. **Understand.** `summarize-source` reads only specified sources; `survey` plans
   and gathers evidence; `teach` captures the user's explanation. `study-book`
   and `study-topic` track progress separately from article content.
2. **Draft.** `write-article` turns sufficient understanding into
   `<store>/drafts/<slug>.md`, including provenance and scoped `claims`. Do not
   commit or search-publish it. Nested workflows return material to their caller
   so only the top-level workflow creates a draft.
3. **Human review.** Present the complete draft, proposed destination, change type
   and claims. Correct gaps and contradictions in the body, not just in chat.
   Generate a browser URL with `article_url.py` and `KNOWLEDGE_UI_BASE_URL` if needed.
4. **Stock.** After explicit approval of the presented content, move it to
   `<store>/articles/<theme>/<slug>.md`, preserving its authored frontmatter.
   Validate and commit the article and its required assets together. A theme INDEX
   is not required and must not become a generated claim inventory.
5. **Search.** Live claim discovery sees adopted files immediately. Explicitly
   refresh your configured backend for indexed article discovery; wait for success.
   On backend failure, live entries remain and grep is a separate explicit choice.
6. **Browse.** Open the original through the UI. The UI can preview drafts and
   learning maps independently of search publication.

A sample claim is:

```yaml
---
source: "Synthetic worked example"
claims:
  - "For equal weights, divide the sum by the count — [calculation](window-mean.md#calculation)."
---
```

Use a nonempty YAML array of nonempty strings. Empty arrays, nonstrings, duplicate
keys and aliases are rejected. Claims are authored assertions, not generated
summaries. Missing claims are authoring errors detected by the checker, not a
publication flag: placement controls publication.

Search excludes drafts, worklogs, tests/fixtures, hidden paths, assets, generated
notebook views, experiments/evaluations and book/topic learning maps. An explicitly
nested Catalog package cannot reopen work excluded by its containing scope. A
file entrance scopes its parent directory, so only list directories appropriate
for that Catalog's readers. Browsing is broader than search; do not expose the UI
to readers who may not see drafts or the selected packages.

`distill` records adopted terms, rules and consequential decisions in the narrowest
appropriate package, with provenance and applicability intact. Keep these commits
separate from article commits. An agreed correction to an adopted article follows
the replacement workflow at the same slug; Git preserves prior versions.
Learning maps update only after the user's final outcome, in a separate commit.

## Skill setup

The eight skill directories under `skills/` form one interoperating set. Register
that directory using your agent's supported skill mechanism, or point repository
skill entries at the individual directories in this checkout. Preserve their
relative layout: support references resolve back to this checkout's `foundation/`.
Do not copy a SKILL.md alone. For articles intended for this repository, use
its root Catalog; keep private knowledge in a separately selected store.
Configure the MCP adapter for the selected Catalog and tell the agent which store
it may write to. A connected Catalog alone does not authorize writes to its store.

| Skill | Input and output |
|---|---|
| summarize-source | Specified source → faithful understanding → draft |
| survey | Planned question → evidence → draft |
| teach | User interview → confirmed understanding → draft |
| study-book | Book and reading map → one learning unit → review outcome |
| study-topic | Capability goal and study map → one learning unit → progress |
| write-article | Prepared understanding → reviewed article adoption |
| distill | Adopted durable knowledge → glossary/rules/decision record |
| grill-federated | Questions and boundary choices → agreed terms and routing |

Skills are primarily Japanese; setup and contracts also have English explanations.
An agent needs file editing and Git access, source-reading capabilities appropriate
to the requested material, and optional web/PDF tools for research. No particular
web, PDF or image-generation provider is required. If a source cannot be read,
report that limitation rather than inventing content. Independent reviewer agents
are useful when available; a documented separate reread is the fallback.

External grilling/domain-modeling and marimo skills were inspected for dependency
intent, not vendored. Generic interview, glossary/decision and notebook guidance
is included here. The SVG overlap helper uses the Python standard library and is
an approximate diagnostic, not a proof that a drawing is readable. Optional image
generation and marimo execution are not required for the Markdown workflow.
