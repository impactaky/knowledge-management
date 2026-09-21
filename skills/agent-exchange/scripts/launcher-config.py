#!/usr/bin/env python3
"""Resolve and validate the Agent Exchange Launcher config into a JSON snapshot.

Lookup order: an explicit ``AGENT_EXCHANGE_CONFIG``, then
``${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/agent-exchange.toml``.
The file is real TOML parsed with the standard library ``tomllib``:

    [implementation]
    kind = "<herdr kind>"
    args = ["<native arg>", "..."]

A missing file, invalid TOML, a missing ``[implementation]`` section, an empty
kind or a non-string ``args`` array is an explicit error before any agent
starts. Unknown kinds are returned as-is; the resolver never substitutes a
built-in kind or model. A relative ``XDG_CONFIG_HOME`` or ``HOME`` fallback is
rejected instead of being resolved against the current directory.
"""
from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

CONFIG_NAME = "agent-exchange.toml"


class ConfigError(Exception):
    pass


def resolve_path() -> Path:
    override = os.environ.get("AGENT_EXCHANGE_CONFIG")
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

    return {
        "source": "config",
        "config_path": str(path.resolve()),
        "kind": kind,
        "args": list(args),
    }


def main() -> int:
    try:
        snapshot = load(resolve_path())
    except ConfigError as exc:
        print(f"agent-exchange config: {exc}", file=sys.stderr)
        return 1
    json.dump(snapshot, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
