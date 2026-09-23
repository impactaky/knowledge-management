"""Portable skill contracts: configuration boundaries, protocol tests, no vendor metadata.

These tests do not depend on a developer's active catalog, agent product or
Herdr session. Shell-protocol tests use temporary directories and a fake Herdr.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
FOUNDATION = ROOT / "foundation"


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("HERDR_", "AGENT_EXCHANGE_")) or key in {
            "ORDER_CONFIG",
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
        }:
            env.pop(key)
    return env


def default_signals() -> None:
    # A non-interactive shell cannot trap a signal that was ignored on entry,
    # and the test process may have inherited ignored SIGTERM/SIGINT from an
    # earlier suite. Reset them so the scripts run as under a service manager.
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, signal.SIG_DFL)


def run_shell_test(relative: str) -> None:
    script = SKILLS / relative
    assert script.is_file(), f"missing test script: {script}"
    missing = [command for command in ("bash", "git", "jq", "flock", "inotifywait", "python3")
               if shutil.which(command) is None]
    if missing:
        pytest.skip("missing required commands: " + ", ".join(missing))
    # Capture to files, not pipes: a shell contract may leave a short-lived
    # background process that would otherwise keep a pipe open past exit.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            result = subprocess.run(
                ["bash", str(script)],
                cwd=ROOT,
                env=clean_env(),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                timeout=300,
                check=False,
                preexec_fn=default_signals,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"{relative} timed out")
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", "replace")
        stderr = err.read().decode("utf-8", "replace")
    assert result.returncode == 0, (
        f"{relative} failed with {result.returncode}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )


@pytest.mark.parametrize("relative", [
    "agent-exchange/tests/test-agent-exchange.sh",
    "agent-exchange/tests/test-herdr-server.sh",
    "agent-exchange/tests/test-launcher-config.sh",
    "agent-exchange/tests/test-launcher-kinds.sh",
    "agent-exchange/tests/test-systemd.sh",
    "order/tests/test-resolve-config.sh",
    "order/tests/test-prompt-wait.sh",
])
def test_shell_contracts_run_in_isolation(relative):
    run_shell_test(relative)


def test_shared_vocabulary_is_reachable_from_the_common_model():
    context = (FOUNDATION / "CONTEXT.md").read_text(encoding="utf-8")
    index = (FOUNDATION / "INDEX.md").read_text(encoding="utf-8")
    for relative in (
        "modules/knowledge-federation/CONTEXT.md",
        "modules/article-curation/CONTEXT.md",
        "modules/delivery-coordination/CONTEXT.md",
        "modules/agent-exchange/CONTEXT.md",
    ):
        assert relative in context, f"foundation/CONTEXT.md does not reference {relative}"
        assert relative in index, f"foundation/INDEX.md does not reference {relative}"


def test_order_skill_has_no_default_kind_or_model():
    text = (SKILLS / "order" / "SKILL.md").read_text(encoding="utf-8")
    # Config boundary: explicit ORDER_CONFIG then XDG, resolved by the helper.
    assert "ORDER_CONFIG" in text
    assert "XDG_CONFIG_HOME" in text
    assert "resolve-config.py" in text
    assert "config.example.toml" in text
    # No built-in kind/model default.
    for forbidden in ("gpt-", "model_reasoning_effort"):
        assert forbidden not in text, f"order SKILL.md unexpectedly contains {forbidden}"


def test_propose_skill_keeps_design_and_authorization_separate():
    text = (SKILLS / "propose" / "SKILL.md").read_text(encoding="utf-8")
    assert "Design Confirmation" in text
    assert "Implementation Authorization" in text
    assert "design-doc.md" in text
    assert "output-style" not in text
    assert "optional capability" in text


def test_no_vendor_skill_metadata_or_real_config_is_tracked():
    assert not list(SKILLS.rglob("agents/openai.yaml"))
    assert not list(SKILLS.rglob("config.toml"))
    assert list(SKILLS.glob("*/config.example.toml"))
