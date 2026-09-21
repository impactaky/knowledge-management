# 0001. Share Delivery Coordination and Agent Exchange as common core

Date: 2026-09-21

## Status

Accepted.

## Context

Reusable design review, implementation authorization, ordering, and durable
agent-to-agent Request/Response workflows previously lived only in a private
knowledge store. That placement treated portable mechanisms as personal
configuration and duplicated ownership of vocabulary that the common core
already provides for Knowledge Federation and Article Curation.

Two workflows needed a canonical home:

- Delivery Coordination: proposal, design confirmation, implementation
  authorization, order, review boundary, and worklog binding.
- Agent Exchange: one Request and at most one Response over a local exchange
  directory, with explicit Threads for serial continuations.

`order` also drives an external Herdr agent session directly, while Agent
Exchange is a separate file-based handoff. Both are useful to any store that
runs this common core, so neither should require a private repository.

## Decision

The common core owns the reusable vocabulary and workflows:

- [Delivery Coordination](../../modules/delivery-coordination/CONTEXT.md) and
  [Agent Exchange](../../modules/agent-exchange/CONTEXT.md) become shared
  modules alongside Knowledge Federation and Article Curation.
- `propose`, `order`, and `agent-exchange` become portable skills under
  `skills/` with `SKILL.md` and portable supporting files.
- Configuration is selected explicitly, then by XDG, then by the `$HOME`
  fallback. Real user configuration and deployment values stay outside the
  repository; only `config.example.toml` is committed.
- Herdr remains an external system. This repository does not vendor Herdr or
  define a compatibility implementation. `order` uses Herdr directly and does
  not call the file-based Agent Exchange protocol; a shared Herdr session name
  does not connect the two workflows.
- Agent Exchange keeps its current boundary: same host, same OS user, local
  filesystem, at most one Response per Request, serial Threads, file state as
  the source of truth, fail-closed on corrupt state or missing resources, and
  no automatic fallback, queue, retry, or close.
- Private deployment (paths, session names, model choices, exchange roots,
  systemd wiring, and private git history) is not included here.

## Alternatives considered

- Keep the mechanisms in the private store and reference them from here. This
  keeps the common core incomplete and makes every other store depend on one
  private layout.
- Keep the workflows and move only the vocabulary. This splits one model across
  two owners and invites drift between definitions and behavior.
- Vendor Herdr or add a compatibility layer. This expands scope beyond the
  portable coordination contract and is not needed to describe the workflows.

## Consequences

- Users configure `order` and Agent Exchange from their own environment. A
  missing or invalid configuration fails before any agent starts, rather than
  silently using a built-in kind or model.
- The repository stays free of personal paths, private store names, real
  configuration, generated caches, and vendor-specific skill metadata.
- Existing Agent Exchange protocol semantics are preserved and pinned by shell
  tests that use a temporary exchange directory and a fake Herdr.
- Herdr-specific adapters are documented as optional integrations, not as part
  of the protocol definition.
