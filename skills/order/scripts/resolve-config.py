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

An optional ``[route]`` section enables selection through an external router.
``route.services`` maps a router service id to a Herdr kind, and
``route.configs.<config-id>`` maps a router config id to per-service native
arguments. ``--route ROUTE_CONFIG SERVICE`` resolves one such pair; it is an
error unless ``[route]`` exists with ``enabled = true``. The router itself is
never invoked here.
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
DEFAULT_ROUTE_MODE = "balanced"
ROUTE_MODES = ("cheap", "balanced", "best")


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


def parse_route(data: dict) -> dict | None:
    section = data.get("route")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError("[route] must be a table")

    enabled = section.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("[route] enabled must be a boolean")

    mode = section.get("mode", DEFAULT_ROUTE_MODE)
    if not isinstance(mode, str) or mode not in ROUTE_MODES:
        raise ConfigError("[route] mode must be one of: " + ", ".join(ROUTE_MODES))

    services: dict[str, str] = {}
    services_section = section.get("services")
    if services_section is not None:
        if not isinstance(services_section, dict):
            raise ConfigError("[route.services] must be a table")
        for service, kind in services_section.items():
            if not isinstance(kind, str) or not kind.strip():
                raise ConfigError(
                    f"route service kind for {service!r} must be a non-empty string"
                )
            services[service] = kind

    configs: dict[str, dict[str, list[str]]] = {}
    configs_section = section.get("configs")
    if configs_section is not None:
        if not isinstance(configs_section, dict):
            raise ConfigError("[route.configs] must be a table")
        for config_id, table in configs_section.items():
            if not isinstance(table, dict):
                raise ConfigError(f"[route.configs.{config_id}] must be a table")
            resolved: dict[str, list[str]] = {}
            for service, args in table.items():
                if service not in services:
                    raise ConfigError(
                        f"route config {config_id!r} references an unknown service: {service!r}"
                    )
                if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
                    raise ConfigError(
                        f"route config {config_id!r} args for {service!r} "
                        "must be an array of strings"
                    )
                resolved[service] = list(args)
            configs[config_id] = resolved

    if enabled:
        if not services:
            raise ConfigError("[route] enabled requires a non-empty [route.services]")
        if not configs:
            raise ConfigError("[route] enabled requires a non-empty [route.configs]")

    return {
        "enabled": enabled,
        "mode": mode,
        "services": services,
        "configs": configs,
    }


def load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid config at {path}: {exc}") from exc

    candidates = parse_candidates(data)
    default = parse_default(data, candidates)
    if "implementation" not in data and "implementations" not in data:
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
        "route": parse_route(data),
    }


def route_summary(route: dict | None) -> dict:
    if route is None:
        return {"enabled": False, "mode": DEFAULT_ROUTE_MODE}
    return {"enabled": route["enabled"], "mode": route["mode"]}


def list_snapshot(config: dict) -> dict:
    default = config["default"]
    default_name = default["name"] if default is not None and default["form"] == "name" else None
    return {
        "config_path": config["config_path"],
        "default": default_name,
        "implementations": [dict(candidate) for candidate in config["candidates"].values()],
        "route": route_summary(config["route"]),
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
        "route": route_summary(config["route"]),
    }


def route_snapshot(config: dict, route_config: str, service: str) -> dict:
    route = config["route"]
    if route is None or not route["enabled"]:
        raise ConfigError("--route requires [route] with enabled = true in the config")
    if route_config not in route["configs"]:
        raise ConfigError(f"unknown route config: {route_config!r}")
    if service not in route["services"]:
        raise ConfigError(f"unknown route service: {service!r}")
    if service not in route["configs"][route_config]:
        raise ConfigError(
            f"route config {route_config!r} has no arguments for service {service!r}"
        )

    return {
        "source": "route",
        "config_path": config["config_path"],
        "kind": route["services"][service],
        "args": route["configs"][route_config][service],
        "session": config["session"],
        "worklog_root": config["worklog_root"],
        "implementation": None,
        "label": None,
        "route_config": route_config,
        "route_service": service,
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
    mode.add_argument(
        "--route",
        nargs=2,
        metavar=("ROUTE_CONFIG", "SERVICE"),
        help="resolve a route config/service pair; requires [route] enabled = true",
    )
    args = parser.parse_args(argv)
    try:
        path = resolve_path(args.config)
        config = load(path)
        if args.list:
            snapshot = list_snapshot(config)
        elif args.route:
            route_config, service = args.route
            snapshot = route_snapshot(config, route_config, service)
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
