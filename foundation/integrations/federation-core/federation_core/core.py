"""Catalog-derived search shared by every federation provider.

The catalog is the only source of package scope.  The public search response is
bounded: agents receive current authored claims, short CONTEXT definitions and
source locators, never article body prose or Meilisearch's raw response.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from .publication import EXCLUDED_DIRS, PublicationScope, index_lines, publication_boundary
from .articles import read_article
from .locators import QUERY_MAX_BYTES, current_locator, current_entry, bound_response
from .glossary import current_context_entry

DEFAULT_CATALOG_PATH = None
DEFAULT_MEILI_URL = ""
INDEX_RESULT_LIMIT = 10
ARTICLE_RESULT_LIMIT = 5
FULLTEXT_FILE_LIMIT = 10
FULLTEXT_MATCH_LIMIT = 3
MEILI_CANDIDATE_LIMIT = 50
SEMANTIC_SCORE_THRESHOLD = 0.72
SEMANTIC_RATIO = 0.7
MEILI_REQUEST_TIMEOUT_SECONDS = 10.0
CATALOG_PACKAGE_HEADER_RE = re.compile(r"^##\s+(?:Packages|パッケージ)\s*$")
CATALOG_ENTRY_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\(([^)]+)\)\s*[—–-]\s*(.+?)\s*$")
TEXT_SUFFIX_DENYLIST = {
    ".7z",
    ".avif",
    ".bin",
    ".bmp",
    ".db",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lock",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".pyc",
    ".sqlite",
    ".tar",
    ".webp",
    ".zip",
}
SKIP_DIRS = EXCLUDED_DIRS | {".git", ".venv"}
PUBLIC_RESPONSE_KEYS = {
    "query",
    "deep",
    "semantic_status",
    "index_results",
    "article_results",
    "fulltext_results",
    "warnings",
    "truncated",
    "limits",
    "omitted",
}


@dataclass(frozen=True)
class Package:
    name: str
    description: str
    entry_path: Path
    root: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "entry_path": str(self.entry_path),
            "root": str(self.root),
        }


@dataclass(frozen=True)
class SearchOptions:
    catalog_path: Path
    meili_url: str = DEFAULT_MEILI_URL
    index_result_limit: int = INDEX_RESULT_LIMIT
    article_result_limit: int = ARTICLE_RESULT_LIMIT
    meili_candidate_limit: int = MEILI_CANDIDATE_LIMIT
    semantic_score_threshold: float = SEMANTIC_SCORE_THRESHOLD


def resolve_catalog_path(path: str | Path | None = None) -> Path:
    raw = (
        str(path)
        if path is not None
        else os.environ.get("FEDERATION_CATALOG")
        or os.environ.get("KNOWLEDGE_CATALOG")
        or DEFAULT_CATALOG_PATH
    )
    if not raw:
        raise ValueError("Set FEDERATION_CATALOG or KNOWLEDGE_CATALOG to your CATALOG.md")
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def get_catalog(catalog_path: str | Path | None = None) -> str:
    """Return the authoritative CATALOG.md text."""
    return resolve_catalog_path(catalog_path).read_text(encoding="utf-8")


def parse_catalog(catalog_path: str | Path, *, strict: bool = False) -> list[Package]:
    """Parse package entries from the catalog's ``## Packages`` or ``## パッケージ`` section."""
    catalog = resolve_catalog_path(catalog_path)
    text = catalog.read_text(encoding="utf-8")
    packages: list[Package] = []
    seen_roots: set[Path] = set()
    seen_names: set[str] = set()
    in_package_section = False
    found_section = False

    for line_number, line in enumerate(text.splitlines(), start=1):
        if CATALOG_PACKAGE_HEADER_RE.match(line):
            in_package_section = True
            found_section = True
            continue
        if in_package_section and line.startswith("## "):
            break
        if not in_package_section:
            continue

        match = CATALOG_ENTRY_RE.match(line)
        if not match:
            if strict and line.lstrip().startswith("-"):
                raise ValueError(f"{catalog}:{line_number}: invalid package entry")
            continue
        name, raw_target, description = match.groups()
        entry_path, root = _resolve_catalog_target(catalog, raw_target)
        if root in seen_roots or name in seen_names:
            raise ValueError(f"{catalog}:{line_number}: duplicate package name or root: {name}")
        seen_roots.add(root)
        seen_names.add(name)
        packages.append(
            Package(
                name=name,
                description=description,
                entry_path=entry_path,
                root=root,
            )
        )
    if strict and not found_section:
        raise ValueError(f"{catalog}: missing package section")
    return packages


def search(
    query: str,
    *,
    deep: bool = True,
    catalog_path: str | Path | None = None,
    meili_url: str | None = None,
) -> dict[str, Any]:
    """Search the live catalog-derived corpus using the public contract."""
    stripped = query.strip()
    if not stripped:
        raise ValueError("query must be a non-empty string")

    if len(stripped.encode("utf-8")) > QUERY_MAX_BYTES:
        return bound_response(
            {
                "query": "", "deep": deep, "semantic_status": "not_requested",
                "index_results": [], "article_results": [], "fulltext_results": [],
                "warnings": [f"query exceeds {QUERY_MAX_BYTES} UTF-8 bytes; search not run"],
                "truncated": True,
            },
            query_omitted=True,
        )
    catalog = resolve_catalog_path(catalog_path)
    options = SearchOptions(
        catalog_path=catalog,
        meili_url=meili_url or os.environ.get("MEILI_URL") or DEFAULT_MEILI_URL,
    )
    packages = parse_catalog(catalog, strict=True)
    scopes = {
        package.root: PublicationScope(
            package.root,
            boundary=publication_boundary(package.root, [p.root for p in packages], catalog=catalog),
        )
        for package in packages
    }

    grep_index = _grep_index_results(stripped, packages, scopes=scopes)
    meili = _meili_search(stripped, deep=deep, options=options)
    index_results, index_truncated = _merge_index_results(
        meili["index_candidates"],
        grep_index,
        packages=packages,
        scopes=scopes,
        limit=options.index_result_limit,
    )

    article_results: list[dict[str, Any]] = []
    article_truncated = False
    if deep:
        article_results, _, article_truncated = _group_article_results(
            meili["article_candidates"],
            packages=packages,
            scopes=scopes,
            limit=options.article_result_limit,
            minimum_score=options.semantic_score_threshold,
        )

    response = {
        "query": stripped,
        "deep": deep,
        "semantic_status": meili["semantic_status"],
        "index_results": index_results,
        "article_results": article_results,
        "fulltext_results": [],
        "warnings": meili["warnings"],
        "truncated": bool(
            index_truncated
            or article_truncated
            or meili["truncated"]
        ),
    }
    response = bound_response(response)
    assert set(response) == PUBLIC_RESPONSE_KEYS
    return response


def grep(
    query: str,
    *,
    catalog_path: str | Path | None = None,
) -> dict[str, Any]:
    """Find literal, case-insensitive locations without a search backend."""
    stripped = query.strip()
    if not stripped:
        raise ValueError("query must be a non-empty string")
    if len(stripped.encode("utf-8")) > QUERY_MAX_BYTES:
        return bound_response(
            {
                "query": "", "fulltext_results": [],
                "warnings": [f"query exceeds {QUERY_MAX_BYTES} UTF-8 bytes; grep not run"],
                "truncated": True,
            },
            query_omitted=True,
        )
    catalog = resolve_catalog_path(catalog_path)
    packages = parse_catalog(catalog, strict=True)
    scopes = {
        package.root: PublicationScope(
            package.root,
            boundary=publication_boundary(package.root, [p.root for p in packages], catalog=catalog),
        )
        for package in packages
    }
    results, truncated = _grep_fulltext_results(
        stripped, packages, scopes=scopes,
        file_limit=FULLTEXT_FILE_LIMIT, match_limit=FULLTEXT_MATCH_LIMIT,
    )
    return bound_response({
        "query": stripped, "fulltext_results": results,
        "warnings": [], "truncated": truncated,
    })


def _resolve_catalog_target(catalog_path: Path, raw_target: str) -> tuple[Path, Path]:
    target = raw_target.split("#", 1)[0].strip()
    parsed = urlparse(target)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"Catalog targets must be local paths or file URLs: {raw_target}")
    elif parsed.scheme == "file":
        path = Path(unquote(parsed.path))
    else:
        path = Path(unquote(target))
        if not path.is_absolute():
            path = catalog_path.parent / path

    path = path.expanduser().resolve()
    # A trailing slash explicitly selects a directory, even when it is missing.
    # Keep the actual entrance: a directory never implies an INDEX.md file.
    if unquote(parsed.path).endswith("/") or path.is_dir():
        return path, path
    if path.is_file() or path.suffix:
        return path, path.parent
    return path, path


def _line_matches(line: str, query: str) -> bool:
    return query.casefold() in line.casefold()


def _is_probably_text(path: Path) -> bool:
    if path.name.startswith("."):
        return False
    if path.suffix.lower() in TEXT_SUFFIX_DENYLIST:
        return False
    try:
        return path.stat().st_size <= 2_000_000
    except OSError:
        return False


def _iter_files(root: Path, *, index_only: bool) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in relative_parts):
            continue
        try:
            is_file = path.is_file()
        except OSError:
            continue
        if not is_file:
            continue
        if index_only and path.name.casefold() not in {"index.md", "context.md"}:
            continue
        if not index_only and not _is_probably_text(path):
            continue
        yield path


def _grep_index_results(
    query: str, packages: list[Package], *, scopes: dict[Path, PublicationScope],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for package in packages:
        if not package.root.exists():
            continue
        for path in _iter_files(package.root, index_only=False):
            if _owning_package(path, packages) != package:
                continue
            scope = scopes[package.root]
            if not scope.allows(path):
                continue
            if scope.allows_article(path):
                numbered_lines = [(c.line, c.text) for c in read_article(path).claims]
                kind = "article-claim"
            elif path.name.casefold() in {"index.md", "context.md"}:
                numbered_lines = index_lines(path)
                kind = "context" if path.name.casefold() == "context.md" else "index"
            else:
                continue
            for line_number, line in numbered_lines:
                if not _line_matches(line, query) or not scope.allows_line(path, line):
                    continue
                results.append(
                    {
                        "package": package.name,
                        "path": str(path.resolve()),
                        "line": line_number,
                        "text": line if kind == "article-claim" else line.strip(),
                        "kind": kind,
                    }
                )
    return results


def _merge_index_results(
    meili_rows: list[dict[str, Any]],
    grep_rows: list[dict[str, Any]],
    *,
    packages: list[Package],
    scopes: dict[Path, PublicationScope],
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    meili_ranges: dict[str, list[tuple[int, int]]] = {}

    def append_row(row: dict[str, Any]) -> bool:
        path = _absolute_path(row.get("path") or row.get("file"))
        if not path:
            return False
        owner = _owning_package(Path(path), packages)
        if owner is None or not scopes[owner.root].allows(Path(path)):
            return False
        if Path(path).name.casefold() == "context.md":
            entry = current_context_entry(Path(path), row)
        else:
            current = current_entry(Path(path), row)
            entry = (*current, None) if current else None
        if entry is None:
            return False
        line, text, definition = entry
        if not scopes[owner.root].allows_line(Path(path), text):
            return False
        kind = "article-claim" if scopes[owner.root].allows_article(Path(path)) else ("index" if Path(path).name.casefold() == "index.md" else "context")
        key = (path, line, text)
        if key in seen:
            return False
        seen.add(key)
        normalized: dict[str, Any] = {
            "package": owner.name,
            "path": path,
            **current_locator(Path(path), line=line, scope=scopes[owner.root],
                              claim_text=text if kind == "article-claim" else None,
                              definition=definition),
            "kind": kind,
        }
        if line is not None:
            normalized["line"] = line
        merged.append(normalized)
        return True

    for row in meili_rows:
        if not append_row(row):
            continue
        path = _absolute_path(row.get("path") or row.get("file"))
        start = _positive_int(row.get("line"))
        end = _positive_int(row.get("end_line")) or start
        # CONTEXT duplicates use their resolved term position. Cached ranges
        # may now cover a different term after a source edit or move.
        if row.get("kind") == "article-claim" or Path(path).name.casefold() == "context.md":
            continue
        if path and start is not None and end is not None:
            meili_ranges.setdefault(path, []).append((start, end))

    for row in grep_rows:
        path = _absolute_path(row.get("path") or row.get("file"))
        line = _positive_int(row.get("line"))
        if path and line is not None and any(
            start <= line <= end for start, end in meili_ranges.get(path, [])
        ):
            continue
        append_row(row)
    public_rows = merged[:limit]
    return public_rows, len(merged) > limit


def _grep_fulltext_results(
    query: str,
    packages: list[Package],
    *,
    scopes: dict[Path, PublicationScope],
    file_limit: int,
    match_limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    results: list[dict[str, Any]] = []
    truncated = False
    seen_paths: set[str] = set()

    for package in packages:
        if not package.root.exists():
            continue
        for path in _iter_files(package.root, index_only=False):
            if _owning_package(path, packages) != package:
                continue
            scope = scopes[package.root]
            if not scope.allows(path):
                continue
            absolute_path = str(path.resolve())
            if absolute_path in seen_paths:
                continue
            seen_paths.add(absolute_path)
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            matches: list[dict[str, Any]] = []
            path_has_overflow = False
            if scope.allows_article(path):
                article = read_article(path)
                numbered_lines = sorted([
                    *article.body_lines,
                    *((claim.line, claim.text) for claim in article.claims),
                ], key=lambda item: item[0])
            else:
                numbered_lines = (
                    index_lines(path) if path.name.casefold() == "index.md"
                    else enumerate(lines, start=1)
                )
            for line_number, line in numbered_lines:
                if not _line_matches(line, query) or not scope.allows_line(path, line):
                    continue
                if any(match["line"] == line_number for match in matches):
                    continue
                if len(matches) >= match_limit:
                    path_has_overflow = True
                    continue
                matches.append({"line": line_number})
            if not matches:
                continue
            if len(results) >= file_limit:
                truncated = True
                return results, truncated
            results.append(
                {
                    "package": package.name,
                    "path": absolute_path,
                    **current_locator(path, line=matches[0]["line"], scope=scope),
                    "match_lines": [match["line"] for match in matches],
                }
            )
            truncated = truncated or path_has_overflow
    return results, truncated


def _owning_package(path: Path, packages: list[Package]) -> Package | None:
    """Return the most-specific catalog package containing ``path``."""
    resolved = path.resolve()
    owners: list[Package] = []
    for package in packages:
        try:
            resolved.relative_to(package.root)
        except ValueError:
            continue
        owners.append(package)
    if not owners:
        return None
    return max(owners, key=lambda package: len(package.root.parts))


def _meili_search(query: str, *, deep: bool, options: SearchOptions) -> dict[str, Any]:
    warnings: list[str] = []
    result: dict[str, Any] = {
        "semantic_status": "not_requested" if not deep else "unavailable",
        "index_candidates": [],
        "article_candidates": [],
        "warnings": warnings,
        "truncated": False,
    }
    if not options.meili_url:
        if deep:
            warnings.append("MEILI_URL is not configured; using live entries only")
        return result
    try:
        _meili_request(options.meili_url, "GET", "/health")
    except Exception:
        warnings.append("Meilisearch unavailable; using live INDEX/CONTEXT and article-claim entries only")
        return result

    try:
        entries_response = _meili_request(
            options.meili_url,
            "POST",
            "/indexes/entries/search",
            {
                "q": query,
                "limit": options.meili_candidate_limit,
                "rankingScoreThreshold": options.semantic_score_threshold,
                "showRankingScore": True,
                "attributesToRetrieve": [
                    "kind", "text", "package", "file", "line", "end_line"
                ],
            },
        )
        entries = _hits(entries_response)
        result["index_candidates"] = [_normalize_index_hit(hit) for hit in entries]
        result["truncated"] = result["truncated"] or _response_truncated(
            entries_response, len(entries)
        )
    except Exception:
        warnings.append("entries lexical search failed; using live INDEX/CONTEXT and article-claim entries")

    if not deep:
        return result

    hybrid_payload = {
        "q": query,
        "limit": options.meili_candidate_limit,
        "hybrid": {"semanticRatio": SEMANTIC_RATIO, "embedder": "default"},
        "rankingScoreThreshold": options.semantic_score_threshold,
        "showRankingScore": True,
        "attributesToRetrieve": [
            "package",
            "title",
            "section",
            "file",
            "anchor",
            "start_line",
            "end_line",
        ],
    }
    try:
        chunks_response = _meili_request(
            options.meili_url,
            "POST",
            "/indexes/chunks/search",
            hybrid_payload,
        )
        result["semantic_status"] = "available"
    except Exception:
        result["semantic_status"] = "degraded"
        warnings.append("semantic search failed; using lexical article search and live entries")
        lexical_payload = dict(hybrid_payload)
        lexical_payload.pop("hybrid")
        try:
            chunks_response = _meili_request(
                options.meili_url,
                "POST",
                "/indexes/chunks/search",
                lexical_payload,
            )
        except Exception:
            warnings.append("chunks lexical search failed; using entries only")
            return result

    chunks = _hits(chunks_response)
    result["article_candidates"] = [
        normalized
        for hit in chunks
        if (normalized := _normalize_article_hit(hit))["score"]
        >= options.semantic_score_threshold
    ]
    result["truncated"] = result["truncated"] or _response_truncated(
        chunks_response, len(chunks)
    )
    return result


def _hits(response: dict[str, Any]) -> list[dict[str, Any]]:
    hits = response.get("hits", [])
    if not isinstance(hits, list):
        return []
    return [hit for hit in hits if isinstance(hit, dict)]


def _response_truncated(response: dict[str, Any], returned: int) -> bool:
    total = response.get("estimatedTotalHits", response.get("totalHits"))
    return isinstance(total, int) and total > returned


def _normalize_index_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "package": str(hit.get("package", "")),
        "path": _absolute_path(hit.get("file")),
        "line": _positive_int(hit.get("line")),
        "end_line": _positive_int(hit.get("end_line")),
        "text": str(hit.get("text", "")) if hit.get("kind") == "article-claim" else str(hit.get("text", "")).strip(),
        "kind": str(hit.get("kind", "index")),
        "score": _score(hit),
    }


def _normalize_article_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "package": str(hit.get("package", "articles")),
        "path": _absolute_path(hit.get("file")),
        "title": str(hit.get("title", "")),
        "section": str(hit.get("section", "")),
        "anchor": str(hit.get("anchor", "")),
        "score": _score(hit),
        "line_start": _positive_int(hit.get("start_line")),
        "line_end": _positive_int(hit.get("end_line")),
    }


def _group_article_results(
    candidates: list[dict[str, Any]],
    *,
    packages: list[Package],
    scopes: dict[Path, PublicationScope],
    limit: int,
    minimum_score: float,
) -> tuple[list[dict[str, Any]], list[tuple[str, int, int]], bool]:
    representatives: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        path = candidate.get("path", "")
        if not path or candidate["score"] < minimum_score:
            continue
        owner = _owning_package(Path(path), packages)
        if owner is None or not scopes[owner.root].allows_article(Path(path)):
            continue
        scoped = {**candidate, "package": owner.name}
        if path not in representatives:
            representatives[path] = scoped

    # Hybrid order is the engine's ranking, not comparable raw-score order.
    ranked = list(representatives.values())
    selected = ranked[:limit]
    public_rows = [
        {
            "package": row["package"],
            "path": row["path"],
            **current_locator(
                Path(row["path"]), section=row["section"],
                scope=scopes[_owning_package(Path(row["path"]), packages).root],
            ),
            "score": row["score"],
        }
        for row in selected
    ]
    # Retain the tuple shape consumed by the recorded experiment runner.
    # Search no longer computes or consumes ranges for fulltext suppression.
    return public_rows, [], len(ranked) > limit


def _score(hit: dict[str, Any]) -> float:
    raw = hit.get("_rankingScore", hit.get("ranking_score", 0.0))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _absolute_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve())


def _meili_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key := os.environ.get("MEILI_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=MEILI_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as exc:
        try:
            message = exc.read().decode("utf-8", errors="replace")
        except Exception:
            message = str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    if not raw:
        return {}
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response from Meilisearch: {exc}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("unexpected non-object response from Meilisearch")
    return decoded
