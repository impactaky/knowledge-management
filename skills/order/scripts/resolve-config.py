#!/usr/bin/env python3
"""Resolve and validate the order implementation config into a JSON snapshot.

Lookup order: an explicit ``--config``, then ``ORDER_CONFIG``, then
``${XDG_CONFIG_HOME:-$HOME/.config}/knowledge-management/order.toml``. This file
belongs to the order skill, not to the implementation repository. A missing
file, invalid TOML, malformed candidate, conflicting default form, invalid
default reference, empty name/label/kind, non-string args, or a non-absolute
optional worklog root is an explicit error so no agent is started with an
ambiguous implementation choice.

The config owns the user's configured implementation choices, not a vendor's
live list of available models. ``--list`` prints the configured choices and
``--implementation NAME`` selects one for a single order. Neither contacts a
provider or launches an agent, and native arguments stay opaque: they are never
shell-evaluated, split or reordered.
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


def require_name(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ConfigError(f"{where} must be a non-empty string without whitespace")
    return value


def parse_candidates(data: dict) -> dict[str, dict]:
    section = data.get("implementations")
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ConfigError("[implementations] must be a table")
    candidates: dict[str, dict] = {}
    for key, table in section.items():
        name = require_name(key, "implementation name")
        if not isinstance(table, dict):
            raise ConfigError(f"[implementations.{name}] must be a table")
        label = table.get("label", name)
        if not isinstance(label, str) or not label.strip():
            raise ConfigError(f"implementation label for {name!r} must be a non-empty string")
        kind = table.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise ConfigError(f"implementation kind for {name!r} must be a non-empty string")
        args = table.get("args")
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ConfigError(f"implementation args for {name!r} must be an array of strings")
        candidates[name] = {"name": name, "label": label, "kind": kind, "args": list(args)}
    return candidates


def parse_default(data: dict, candidates: dict[str, dict]) -> dict | None:
    section = data.get("implementation")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError("[implementation] must be a table")

    has_name = "name" in section
    has_inline = "kind" in section or "args" in section
    if has_name and has_inline:
        raise ConfigError("implementation must not mix a name reference with inline kind/args")
    if has_name:
        name = require_name(section.get("name"), "implementation name")
        if name not in candidates:
            raise ConfigError(f"implementation name references an unknown candidate: {name!r}")
        return {"form": "name", "name": name}
    if not has_inline:
        raise ConfigError("implementation must define either inline kind/args or a name reference")

    kind = section.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ConfigError("implementation kind must be a non-empty string")
    args = section.get("args")
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ConfigError("implementation args must be an array of strings")
    return {"form": "inline", "kind": kind, "args": list(args)}


def load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid config at {path}: {exc}") from exc

    candidates = parse_candidates(data)
    default = parse_default(data, candidates)
    if default is None and not candidates:
        raise ConfigError(
            f"config at {path} must define [implementation] or [implementations]"
        )

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
        "config_path": str(path.resolve()),
        "session": session,
        "worklog_root": worklog_root,
        "candidates": candidates,
        "default": default,
    }


def list_snapshot(config: dict) -> dict:
    default = config["default"]
    default_name = default["name"] if default is not None and default["form"] == "name" else None
    return {
        "config_path": config["config_path"],
        "default": default_name,
        "implementations": [dict(candidate) for candidate in config["candidates"].values()],
    }


def resolve_snapshot(config: dict, requested: str | None) -> dict:
    if requested is not None:
        candidate = config["candidates"].get(requested)
        if candidate is None:
            raise ConfigError(f"unknown implementation: {requested!r}")
        name = candidate["name"]
        label = candidate["label"]
        kind = candidate["kind"]
        args = candidate["args"]
    else:
        default = config["default"]
        if default is None:
            raise ConfigError(
                "no default implementation configured; add [implementation] or select one "
                "with --implementation"
            )
        if default["form"] == "inline":
            name = None
            label = None
            kind = default["kind"]
            args = default["args"]
        else:
            candidate = config["candidates"][default["name"]]
            name = candidate["name"]
            label = candidate["label"]
            kind = candidate["kind"]
            args = candidate["args"]

    return {
        "source": "config",
        "config_path": config["config_path"],
        "kind": kind,
        "args": args,
        "session": config["session"],
        "worklog_root": config["worklog_root"],
        "implementation": name,
        "label": label,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="explicit config path; overrides ORDER_CONFIG and XDG")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="list configured implementations as JSON")
    mode.add_argument(
        "--implementation",
        metavar="NAME",
        help="select one configured implementation for this order",
    )
    args = parser.parse_args(argv)
    try:
        path = resolve_path(args.config)
        config = load(path)
        if args.list:
            snapshot = list_snapshot(config)
        else:
            snapshot = resolve_snapshot(config, args.implementation)
    except ConfigError as exc:
        print(f"order config: {exc}", file=sys.stderr)
        return 1
    json.dump(snapshot, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
