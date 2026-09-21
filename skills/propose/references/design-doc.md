# Design document format

This is the self-contained format and discipline for the single design document
that `propose` maintains. These rules are the canonical ones; no private style
file is required.

## Design phase: exactly three sections

```markdown
# <name>

## 実装案
## 仮定したこと
## 確認項目
```

- **実装案**: the problem being solved, the state to reach, and only the
  structure or flow that a design decision depends on.
- **仮定したこと**: assumptions that investigation could not settle but that the
  design needs in order to stand.
- **確認項目**: only points whose answers change the design, each with a short
  recommended answer and reason. Do not list alternative choices and their
  effects.

There is no fixed limit on characters, items, or screens. Keep it short by
omitting whatever does not change the current understanding or decision.
Out-of-scope items, concerns, and open questions are not mandatory headings;
include them in the natural place within the three sections only when they
matter now. Never hide unresolved uncertainty as if it were settled: put it
visibly in 仮定したこと or 確認項目. Do not proceed with irreversible,
high-cost, or dangerous actions until the answer is in.

## Revision loop

- Answer confirmations by number and diff; do not ask independent questions one
  at a time.
- Revise the whole document on every answer and show the changes first.
- Never ask the same thing twice.
- Agreement is about the design and shared understanding, not about the text.
  Keep iterating until terms stop moving, premises stop being corrected, and no
  new background appears.
- Write contested terms into the relevant `CONTEXT.md` as soon as they settle,
  using the vocabulary and boundary rules from `grill-federated`.
- Do not treat the design as confirmed until the shared understanding is
  confirmed. If the user cuts the loop short, state the unconverged items and
  stop.

## From design to order

Design confirmation does not authorize implementation. Only a separate
implementation request plus approval of the execution plan starts work. When it
does, expand the same document into four self-contained sections so a worker
that has no conversation history can implement and judge completion:

```markdown
## 実装対象
## Work
## Acceptance criteria
## Verification
```

Drop 確認項目, keep the design-phase content as premises, and record the
implementation selection source, resolved kind, order-preserving native args,
and (when config-resolved) the resolved config path. Hand the order workflow in
`order` the rest of the procedure rather than duplicating it.
