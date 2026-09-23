#!/usr/bin/env python3
"""Prompt a Herdr agent, or wait on it, until its settled state holds.

``herdr agent prompt --wait`` and ``herdr agent wait`` return on the first
settled state (``idle``, ``done`` or ``blocked``). Some agent integrations
report a transient settled state while a turn is still starting: an OpenCode
v2 TUI, for example, can report ``idle`` during the first turn of a fresh
session and ``working`` again a moment later, so the wait returns while the
agent is still working. This wrapper confirms that the settled state holds:
after each settled return it pauses for ``--settle-ms``, reads the agent with
``agent get``, and waits again while the agent reports ``working``. The check
is kind-agnostic and costs one pause when the first result was already true.

Without ``--wait-only`` the prompt is read from ``--prompt-file`` or stdin and
passed to ``agent prompt`` as a single argument, never shell-evaluated. With
``--wait-only`` no input is sent. ``--timeout`` bounds the whole call,
including the pauses. The executable is ``herdr`` unless ``HERDR`` names
another one.

On success stdout is the ``agent get`` JSON observed after the state held.
A Herdr error (for example ``agent_prompt_stalled``, ``agent_blocked`` or a
timeout) is passed through unchanged with Herdr's exit status, and no re-wait
is attempted.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

SETTLED = frozenset({"idle", "done", "blocked"})


class Deadline:
    def __init__(self, timeout_ms: int | None) -> None:
        self.end = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000

    def remaining_ms(self) -> int | None:
        if self.end is None:
            return None
        return max(0, int((self.end - time.monotonic()) * 1000))

    def expired(self) -> bool:
        return self.end is not None and time.monotonic() >= self.end


def herdr(session: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = [os.environ.get("HERDR", "herdr"), "--session", session, *args]
    return subprocess.run(command, capture_output=True, text=True)


def timeout_args(deadline: Deadline) -> list[str]:
    remaining = deadline.remaining_ms()
    return [] if remaining is None else ["--timeout", str(max(1, remaining))]


def status_of(output: str) -> str | None:
    try:
        agent = json.loads(output)["result"]["agent"]
    except (ValueError, KeyError, TypeError):
        return None
    status = agent.get("agent_status") if isinstance(agent, dict) else None
    return status if isinstance(status, str) else None


def passthrough(result: subprocess.CompletedProcess[str]) -> int:
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode or 1


def timed_out(agent: str) -> int:
    message = f"settled state of {agent} did not hold before the timeout"
    print(json.dumps({"error": {"code": "timeout", "message": message}}))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("agent", help="Herdr agent name")
    parser.add_argument("--session", required=True, help="Herdr session name")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--prompt-file", help="read the prompt from this file instead of stdin")
    source.add_argument("--wait-only", action="store_true", help="wait without sending a prompt")
    parser.add_argument("--timeout", type=int, help="overall limit in milliseconds (default: none)")
    parser.add_argument("--settle-ms", type=int, default=2000, help="pause before confirming a settled state")
    options = parser.parse_args()
    if options.timeout is not None and options.timeout <= 0:
        parser.error("--timeout must be positive")
    if options.settle_ms < 0:
        parser.error("--settle-ms must not be negative")

    deadline = Deadline(options.timeout)
    if options.wait_only:
        result = herdr(options.session, "agent", "wait", options.agent, *timeout_args(deadline))
    else:
        if options.prompt_file:
            with open(options.prompt_file, encoding="utf-8") as handle:
                prompt = handle.read()
        else:
            prompt = sys.stdin.read()
        if not prompt.strip():
            parser.error("the prompt is empty")
        result = herdr(
            options.session, "agent", "prompt", options.agent, prompt, "--wait", *timeout_args(deadline)
        )

    rewaits = 0
    while True:
        if result.returncode != 0 or status_of(result.stdout) not in SETTLED:
            return passthrough(result)
        time.sleep(options.settle_ms / 1000)
        if deadline.expired():
            return timed_out(options.agent)
        current = herdr(options.session, "agent", "get", options.agent)
        if current.returncode != 0:
            return passthrough(current)
        if status_of(current.stdout) != "working":
            if rewaits:
                print(f"prompt-wait: waited again {rewaits} time(s) after a transient settled state", file=sys.stderr)
            sys.stdout.write(current.stdout)
            return 0
        if deadline.expired():
            return timed_out(options.agent)
        rewaits += 1
        result = herdr(options.session, "agent", "wait", options.agent, *timeout_args(deadline))


if __name__ == "__main__":
    sys.exit(main())
