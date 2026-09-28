#!/usr/bin/env python3
"""Build the first worker prompt from the fixed prefix, project rules and order.

The worker prefix lives in ``worker-prompt.md`` next to this script and is the
single source of truth for the implementation worker's instructions; the order
skill does not duplicate it. The order config is loaded and validated by the
same code path as ``resolve-config.py``: an explicit ``--config``, then
``ORDER_CONFIG``, then the XDG or HOME default. The optional
``[context] project_rules`` value is an absolute path template whose only
placeholder is ``{repo}``, expanded to the basename of the real path passed as
``--repo``.

The three bodies are joined deterministically as the prefix, then an optional
``# Project rules`` section, then ``# Order``. A missing config file, an absent
``[context]`` or a missing rules file omits the project rules and reports a
one-line notice on stderr. An invalid config, an unreadable rules file or a
failed save is an error that writes nothing to stdout and saves nothing. On
success the same bytes are saved to ``prompt.md`` beside the order before they
are written to stdout, so the sent prompt and the recorded one cannot diverge.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKER_PROMPT_PATH = SCRIPT_DIR.parent / "worker-prompt.md"


class BuildError(Exception):
    pass


def load_resolver():
    spec = importlib.util.spec_from_file_location(
        "order_resolve_config", SCRIPT_DIR / "resolve-config.py"
    )
    if spec is None or spec.loader is None:
        raise BuildError("could not load resolve-config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize(text: str) -> str:
    return text.rstrip("\n")


def read_body(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildError(f"cannot read {what} at {path}: {exc}") from exc


def project_rules_body(resolver, explicit: str | None, repo: Path) -> str | None:
    try:
        config_path = resolver.resolve_path(explicit)
    except resolver.ConfigError as exc:
        raise BuildError(str(exc)) from exc
    if not config_path.exists():
        print(
            f"build-prompt: config not found at {config_path}; "
            "continuing without project rules",
            file=sys.stderr,
        )
        return None
    try:
        config = resolver.load(config_path)
    except resolver.ConfigError as exc:
        raise BuildError(str(exc)) from exc
    template = config["project_rules"]
    if template is None:
        print(
            "build-prompt: no [context] project_rules configured; "
            "continuing without project rules",
            file=sys.stderr,
        )
        return None
    expanded = Path(template.replace("{repo}", repo.name))
    if not expanded.exists():
        print(
            f"build-prompt: project rules file not found at {expanded}; "
            "continuing without project rules",
            file=sys.stderr,
        )
        return None
    return normalize(read_body(expanded, "project rules file"))


def build(resolver, order: Path, repo: Path, explicit: str | None) -> str:
    prefix = normalize(read_body(WORKER_PROMPT_PATH, "worker-prompt.md"))
    rules = project_rules_body(resolver, explicit, repo)
    order_body = normalize(read_body(order, "order file"))
    sections = [prefix]
    if rules is not None:
        sections.append("# Project rules\n\n" + rules)
    sections.append("# Order\n\n" + order_body)
    return "\n\n".join(sections) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--order", required=True, help="path to the order markdown file")
    parser.add_argument("--repo", required=True, help="target repository root directory")
    parser.add_argument(
        "--config", help="explicit config path; overrides ORDER_CONFIG and XDG"
    )
    options = parser.parse_args(argv)

    order = Path(options.order)
    repo = Path(options.repo)
    if not repo.is_dir():
        parser.error(f"--repo is not a directory: {repo}")

    try:
        resolver = load_resolver()
        prompt = build(resolver, order, repo.resolve(), options.config)
    except BuildError as exc:
        print(f"build-prompt: {exc}", file=sys.stderr)
        return 1

    destination = order.parent / "prompt.md"
    try:
        destination.write_text(prompt, encoding="utf-8")
    except OSError as exc:
        print(f"build-prompt: cannot save {destination}: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
