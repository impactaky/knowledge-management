# knowledge-management

Shared tools for keeping knowledge that people and AI agents can create, review,
search and browse: the core, MCP server, browser UI, indexer, checker, agent
skills and shared rules. Your own knowledge lives in a separate private store
that points back to this checkout, so you receive updates with `git pull`.

**Repository creation has been authorized by the author; project license remains undecided.**
No project-wide license is granted by this repository; see [provenance and notices](docs/provenance.md).

## Install

Requires Linux, Python 3.12+, [uv](https://docs.astral.sh/uv/) and Deno 2.

```bash
git clone https://github.com/impactaky/knowledge-management.git
cd knowledge-management
export UV_CACHE_DIR="$PWD/.cache/uv" DENO_DIR="$PWD/.cache/deno"
uv sync --locked --project foundation/tools/knowledge-ui
deno task --config foundation/tools/knowledge-ui/deno.json build-browser-assets
```

Optional: try the synthetic sample first with
`FEDERATION_CATALOG="$PWD/examples/minimal/CATALOG.md" bash foundation/tools/knowledge-ui/run.sh`
and open <http://127.0.0.1:7776>.

## Create your store

Your store is a separate Git repository. Do not put it inside this checkout.

```bash
cp -R templates/store ~/my-knowledge
git -C ~/my-knowledge init
```

In `~/my-knowledge/CATALOG.md`, change the `shared-rules` path to this checkout's
absolute `foundation/INDEX.md`:

```markdown
- [shared-rules](/home/you/knowledge-management/foundation/INDEX.md) — …
```

## Configure this machine

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management"
cp .env.example "${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/runtime.env"
```

Set `FEDERATION_CATALOG=/home/you/my-knowledge/CATALOG.md` in `runtime.env`.
`run.sh` reads this file by default.

## Check and browse

```bash
uv run --locked --project foundation/tools/knowledge-ui \
  python foundation/tools/knowledge-ui/check.py --catalog ~/my-knowledge/CATALOG.md
bash foundation/tools/knowledge-ui/run.sh
```

The checker should report `0 issue(s)` with no `Advisory`. The browser runs at
<http://127.0.0.1:7776>. Browsing and search work without extra services;
semantic search needs Meilisearch and Ollama ([optional backend](docs/setup.md#optional-backend)).

## Connect your agent

**MCP server** (Claude Code example; other clients use the same command, see [Core and MCP](docs/setup.md#core-and-mcp)):

```bash
claude mcp add knowledge-federation -s user \
  -e UV_CACHE_DIR=$HOME/knowledge-management/.cache/uv -- \
  uv run --locked --directory $HOME/knowledge-management \
  --project foundation/tools/knowledge-ui \
  --env-file $HOME/.config/knowledge-management/runtime.env \
  python foundation/integrations/federation-mcp/server.py
```

The agent gets `get_catalog`, `federation_search` and `federation_grep`.

**Skills**: install the directories under [skills/](skills/) with your usual skill
tool, or symlink them into your agent's skills directory. Always install a whole
directory, never `SKILL.md` alone ([skill setup](docs/workflow.md#skill-setup)).

**Instructions**: the store's `AGENTS.md` already tells agents to follow
shared-rules `docs/agent-setup.md`. To use the store from other repositories too,
add the same line to your user-level agent instructions.

## Daily use

Ask your agent in plain words:

| Say | Skill | Result |
|---|---|---|
| "Research X" | `survey` | A sourced article draft |
| "Summarize this paper" | `summarize-source` | A faithful summary draft of that source only |
| "Let me explain X" | `teach` | An interview that turns what you know into a draft |
| "Make it an article" | `write-article` | After your review, the draft is stocked under `articles/` |
| "Remember this" | `distill` | Durable rules and terms written to the right package |
| "Study this topic / book" | `study-topic` / `study-book` | A learning map you work through step by step |

Keep per-repository notes such as local paths and device IPs in
`projects/<repo>/rules.md` ([projects contract](foundation/docs/projects.md)).

## Update

```bash
git -C ~/knowledge-management pull
# update installed skills with your skill tool
uv run --locked --project foundation/tools/knowledge-ui \
  python foundation/tools/knowledge-ui/check.py --catalog ~/my-knowledge/CATALOG.md
```

If the checker prints an `Advisory`, apply it; [store-changes.md](foundation/docs/store-changes.md)
lists every change a store needs.

## More documentation

- [Installation, configuration, MCP and optional search services](docs/setup.md)
- [Understanding → draft → human review → stock → search → browse](docs/workflow.md)
- [Delivery coordination, order and Agent Exchange](docs/setup.md#delivery-and-agent-exchange-configuration)
- [Search and publication contract](foundation/integrations/federation-core/README.md)
- [Extraction scope and acceptance evidence](docs/extraction.md)

## Maintainers: this repository's articles

The root [CATALOG.md](CATALOG.md) and `articles/` are for articles about this
repository itself. Select it with `export FEDERATION_CATALOG="$PWD/CATALOG.md"`
(and the same absolute path in the MCP server's environment, then restart it).
Draft in `drafts/` and stock reviewed articles under `articles/<theme>/`.

## Verify

```bash
uv run --locked --project foundation/tools/knowledge-ui pytest foundation/tests -q
uv run --locked --project foundation/tools/knowledge-ui python scripts/smoke.py
```

Tests use temporary synthetic stores and mock backends, and never touch running
search services. The suite includes the Agent Exchange and order shell contracts;
see [setup](docs/setup.md#optional-backend) for the disposable real-backend smoke.
