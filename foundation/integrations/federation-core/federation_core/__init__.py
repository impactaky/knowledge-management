"""Catalog-driven search core for knowledge federation providers."""

from .core import (
    ARTICLE_RESULT_LIMIT,
    DEFAULT_CATALOG_PATH,
    DEFAULT_MEILI_URL,
    FULLTEXT_FILE_LIMIT,
    FULLTEXT_MATCH_LIMIT,
    INDEX_RESULT_LIMIT,
    Package,
    PUBLIC_RESPONSE_KEYS,
    SEMANTIC_SCORE_THRESHOLD,
    SearchOptions,
    get_catalog,
    grep,
    parse_catalog,
    search,
)

__all__ = [
    "ARTICLE_RESULT_LIMIT",
    "DEFAULT_CATALOG_PATH",
    "DEFAULT_MEILI_URL",
    "FULLTEXT_FILE_LIMIT",
    "FULLTEXT_MATCH_LIMIT",
    "INDEX_RESULT_LIMIT",
    "Package",
    "PUBLIC_RESPONSE_KEYS",
    "SEMANTIC_SCORE_THRESHOLD",
    "SearchOptions",
    "get_catalog",
    "grep",
    "parse_catalog",
    "search",
]
