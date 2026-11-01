# Optional Herdr review notifier

Set `KNOWLEDGE_REVIEW_NOTIFIER` to the absolute path of executable `notifier.py`.
Only Python's standard library and an installed Herdr CLI are required.
`HERDR_BIN_PATH` selects the CLI (otherwise `herdr` on PATH).

The requesting/replying agent must inherit `HERDR_ENV=1`, `HERDR_PANE_ID` and an
absolute `HERDR_SOCKET_PATH`. `whoami` verifies `agent get` returns that pane and
an `agent_session` with kind `id`; unknown identity exits 2. Any agent kind is
accepted. The destination includes kind, agent, session_id, pane_hint, socket and
title. Treat owner files as trusted local runtime data, never as public content.

`status <owner.json>` and `deliver <owner.json> <message>` use the stored socket
and locate the conversation by session ID in `agent list`. Pane reuse alone
cannot select a recipient. A working recipient waits with
`agent wait <pane> --until idle --timeout 1800000`, then identity is checked again
before `agent prompt`. Blocked recipients are never prompted. No automatic
resume occurs. UI and Herdr must run on the same machine; cross-machine delivery
is unsupported.

Stdout is JSON. Delivery exit codes: 0 delivered, 2 absent, 3 blocked, 4 timeout,
1 other error. Status reports idle/working/blocked/done/absent and optional title.
A blocked/absent/failed notification remains available as a paste line in the UI.
Run synthetic tests from the checkout root:

```bash
uv run --locked --project foundation/tools/knowledge-ui python -m pytest foundation/tests/test_herdr_review_notify.py
```

These tests use a stub executable and never contact a real Herdr session.
