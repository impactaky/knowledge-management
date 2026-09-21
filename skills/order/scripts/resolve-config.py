#!/usr/bin/env python3
"""Resolve and validate the order implementation config into a JSON snapshot.

Lookup order: an explicit ``--config``, then ``ORDER_CONFIG``, then
``${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/order.toml``. This file
belongs to the order skill, not to the implementation repository. A missing
file, invalid TOML, missing section, empty kind, non-string args, or a
non-absolute optional worklog root is an explicit error so no agent is started
with an ambiguous implementation choice.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

CONFIG_NAME = "order.toml"
DEFAULT_SESSION = "agent-exchange"


class ConfigError(Exception):
    pass


def resolve_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    override = os.environ.get("ORDER_CONFIG")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        if not xdg.startswith("/"):
            raise ConfigError(f"XDG_CONFIG_HOME must be an absolute path: {xdg}")
        return Path(xdg) / "knowledge-management" / CONFIG_NAME
    home = os.environ.get("HOME")
    if not home:
        raise ConfigError("HOME must be set to resolve the default config location")
    if not home.startswith("/"):
        raise ConfigError(f"HOME must be an absolute path: {home}")
    return Path(home) / ".config" / "knowledge-management" / CONFIG_NAME


def load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid config at {path}: {exc}") from exc

    implementation = data.get("implementation")
    if not isinstance(implementation, dict):
        raise ConfigError(f"missing [implementation] section in {path}")
    kind = implementation.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ConfigError("implementation kind must be a non-empty string")
    args = implementation.get("args")
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ConfigError("implementation args must be an array of strings")

    session = DEFAULT_SESSION
    herdr = data.get("herdr")
    if herdr is not None:
        if not isinstance(herdr, dict):
            raise ConfigError("[herdr] must be a table")
        candidate = herdr.get("session", DEFAULT_SESSION)
        if not isinstance(candidate, str) or not candidate.strip():
            raise ConfigError("herdr session must be a non-empty string")
        session = candidate

    worklog_root = None
    worklog = data.get("worklog")
    if worklog is not None:
        if not isinstance(worklog, dict):
            raise ConfigError("[worklog] must be a table")
        candidate = worklog.get("root")
        if candidate is not None:
            if not isinstance(candidate, str) or not candidate.startswith("/"):
                raise ConfigError("worklog root must be an absolute path string")
            worklog_root = candidate

    return {
        "source": "config",
        "config_path": str(path.resolve()),
        "kind": kind,
        "args": list(args),
        "session": session,
        "worklog_root": worklog_root,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="explicit config path; overrides ORDER_CONFIG and XDG")
    args = parser.parse_args(argv)
    try:
        path = resolve_path(args.config)
        snapshot = load(path)
    except ConfigError as exc:
        print(f"order config: {exc}", file=sys.stderr)
        return 1
    json.dump(snapshot, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
