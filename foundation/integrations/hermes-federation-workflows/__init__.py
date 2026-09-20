"""Standalone Hermes plugin exposing canonical and optional store federation workflows."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_SKILLS_PATH = REPO_ROOT / "skills"


def discover_workflows(skills_path: Path) -> list[tuple[str, Path, str]]:
    """Read valid SKILL.md frontmatter from a repository skill directory."""
    workflows: list[tuple[str, Path, str]] = []
    if not skills_path.is_dir():
        return workflows
    for skill_path in sorted(skills_path.glob("*/SKILL.md")):
        try:
            content = skill_path.read_text(encoding="utf-8")
            closing = content.find("\n---\n", 4)
            if not content.startswith("---\n") or closing < 0:
                continue
            metadata = yaml.safe_load(content[4:closing])
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            logger.warning("Skipping unreadable workflow skill %s: %s", skill_path, exc)
            continue
        if not isinstance(metadata, dict):
            continue
        name = metadata.get("name")
        description = metadata.get("description")
        if isinstance(name, str) and name and isinstance(description, str) and description:
            workflows.append((name, skill_path, description))
    return workflows


def _federation_config() -> dict:
    try:
        from hermes_cli.config import load_config

        config = load_config()
        memory = config.get("memory", {}) if isinstance(config, dict) else {}
        federation = memory.get("federation", {}) if isinstance(memory, dict) else {}
        return federation if isinstance(federation, dict) else {}
    except Exception as exc:
        logger.warning("Cannot read federation config for workflow plugin: %s", exc)
        return {}


def _hermes_home() -> Optional[Path]:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return None


def _resolve_config_path(path_value: str) -> Path:
    import os
    expanded = os.path.expandvars(os.path.expanduser(path_value))
    path = Path(expanded)
    if not path.is_absolute():
        home = _hermes_home()
        if home is not None:
            path = home / path
    return path.resolve()


def _catalog_path(config: Optional[dict] = None) -> Optional[Path]:
    if config is None:
        config = _federation_config()
    catalog_value = config.get("catalog_path")
    if isinstance(catalog_value, str) and catalog_value.strip():
        return _resolve_config_path(catalog_value.strip())
    return None


def _shared_skills_path(config: dict) -> Path:
    value = config.get("shared_skills_path")
    if isinstance(value, str) and value.strip():
        return _resolve_config_path(value.strip())
    return CANONICAL_SKILLS_PATH


def _personal_skills_path(config: dict) -> Optional[Path]:
    value = config.get("personal_skills_path")
    if isinstance(value, str) and value.strip():
        return _resolve_config_path(value.strip())
    catalog_path = _catalog_path(config)
    if catalog_path is not None:
        candidate = catalog_path.parent / "skills"
        if candidate.is_dir():
            return candidate
    return None


def register(ctx) -> None:
    config = _federation_config()
    registered_names: set[str] = set()

    # 1. Register canonical shared workflows
    shared_path = _shared_skills_path(config)
    for name, skill_path, description in discover_workflows(shared_path):
        if name not in registered_names:
            ctx.register_skill(name, skill_path, description.strip())
            registered_names.add(name)

    # 2. Register optional personal / store workflows without duplicating shared ones
    personal_path = _personal_skills_path(config)
    if personal_path is not None and personal_path.resolve() != shared_path.resolve():
        for name, skill_path, description in discover_workflows(personal_path):
            if name not in registered_names:
                ctx.register_skill(name, skill_path, description.strip())
                registered_names.add(name)
            else:
                logger.debug("Skipping duplicate personal skill %s from %s", name, skill_path)
