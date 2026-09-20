"""Hermes memory provider for the knowledge federation catalog.

The provider is intentionally stateless: CATALOG.md is the source of truth
for both prompt injection and search scope.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

CATALOG_PATH_KEY = "catalog_path"
MEILI_URL_KEY = "meili_url"
CORE_PATH = Path(__file__).resolve().parents[1] / "federation-core"
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from federation_core import search as federation_core_search  # noqa: E402
from federation_core import grep as federation_core_grep  # noqa: E402


SEARCH_SCHEMA: Dict[str, Any] = {
    "name": "federation_search",
    "description": (
        "Search the knowledge federation derived from CATALOG.md. "
        "Default runs full-text hybrid plus Package INDEX/CONTEXT and article claims. "
        "Set deep=false for entries only. Return article locators and entries with current authored "
        "claims and short authored CONTEXT definitions, never article body snippets. "
        "Oversized definitions are omitted whole with a reason. Semantic failures fall back to lexical locators. "
        "Use federation_grep explicitly for literal text locations; use this tool to find knowledge by meaning."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "deep": {
                "type": "boolean",
                "description": "Also run article semantic search.",
                "default": True,
            },
        },
        "required": ["query"],
    },
}


GREP_SCHEMA: Dict[str, Any] = {
    "name": "federation_grep",
    "description": (
        "Find literal text locations, ignoring case, in published Catalog files. "
        "Independent of semantic search and backends; returns current authored "
        "claims and line numbers, never body snippets. "
        "Use federation_search to find knowledge by meaning."
    ),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Literal text to locate."}},
        "required": ["query"],
    },
}


def _json_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _load_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        config = load_config()
        return config if isinstance(config, dict) else {}
    except Exception as exc:
        logger.debug("Failed to load Hermes config: %s", exc)
        return {}


def _provider_config() -> Dict[str, Any]:
    memory = _load_config().get("memory", {})
    if not isinstance(memory, dict):
        return {}
    config = memory.get("federation", {})
    return config if isinstance(config, dict) else {}


def _hermes_home() -> Optional[Path]:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return None


def _resolve_config_path(path_value: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(path_value))
    path = Path(expanded)
    if not path.is_absolute():
        home = _hermes_home()
        if home is not None:
            path = home / path
    return path.resolve()


class FederationMemoryProvider(MemoryProvider):
    """Deterministic bridge from Hermes to the local knowledge federation."""

    def __init__(self, catalog_path: str | None = None):
        self._catalog_path_override = catalog_path
        self._session_id = ""

    @property
    def name(self) -> str:
        return "federation"

    def _catalog_path(self) -> Optional[Path]:
        path_value = self._catalog_path_override or _provider_config().get(CATALOG_PATH_KEY, "")
        if not isinstance(path_value, str) or not path_value.strip():
            return None
        return _resolve_config_path(path_value)

    def is_available(self) -> bool:
        path = self._catalog_path()
        return bool(path and path.is_file())

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": CATALOG_PATH_KEY,
                "description": "Absolute path to the federation CATALOG.md",
                "required": True,
            },
            {
                "key": MEILI_URL_KEY,
                "description": "Optional Meilisearch URL for semantic search (e.g. http://127.0.0.1:7700)",
                "required": False,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Persist non-secret config under memory.federation in config.yaml."""
        try:
            from hermes_cli.config import load_config, save_config

            config = load_config()
            if not isinstance(config, dict):
                config = {}
            memory = config.setdefault("memory", {})
            if not isinstance(memory, dict):
                memory = {}
                config["memory"] = memory
            memory["federation"] = dict(values)
            save_config(config)
            return
        except Exception as exc:
            logger.debug("Falling back to direct YAML config write: %s", exc)

        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml

            config = {}
            if config_path.exists():
                with config_path.open(encoding="utf-8-sig") as handle:
                    loaded = yaml.safe_load(handle) or {}
                    if isinstance(loaded, dict):
                        config = loaded
            memory = config.setdefault("memory", {})
            if not isinstance(memory, dict):
                memory = {}
                config["memory"] = memory
            memory["federation"] = dict(values)
            with config_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
        except Exception:
            logger.exception("Failed to save federation provider config")

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        catalog_path = self._catalog_path()
        if catalog_path is None:
            return ""
        catalog = catalog_path.read_text(encoding="utf-8")
        return (
            "<federation-catalog>\n"
            f"Source: {catalog_path}\n\n"
            f"{catalog.rstrip()}\n"
            "</federation-catalog>"
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        return None

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, GREP_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name not in {"federation_search", "federation_grep"}:
            return _json_error(f"Unknown federation tool: {tool_name}")
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return _json_error("query must be a non-empty string")
        deep = bool(args.get("deep", True))
        try:
            if tool_name == "federation_grep":
                catalog_path, _ = self._catalog_text()
                result = federation_core_grep(query.strip(), catalog_path=catalog_path)
            else:
                result = self._search(query.strip(), deep=deep)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logger.exception("%s failed", tool_name)
            return _json_error(str(exc))

    def _catalog_text(self) -> Tuple[Path, str]:
        catalog_path = self._catalog_path()
        if catalog_path is None:
            raise RuntimeError("memory.federation.catalog_path is not configured")
        if not catalog_path.is_file():
            raise RuntimeError(f"catalog_path does not exist: {catalog_path}")
        return catalog_path, catalog_path.read_text(encoding="utf-8")

    def _meili_url(self) -> str:
        configured = _provider_config().get(MEILI_URL_KEY)
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        return os.environ.get("MEILI_URL", "")

    def _search(self, query: str, *, deep: bool) -> Dict[str, Any]:
        catalog_path, _ = self._catalog_text()
        return federation_core_search(
            query,
            deep=deep,
            catalog_path=catalog_path,
            meili_url=self._meili_url(),
        )


def register(ctx) -> None:
    ctx.register_memory_provider(FederationMemoryProvider())
