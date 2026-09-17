"""Current, authored selection metadata and bounded Agent search envelopes."""

from __future__ import annotations
import json
from pathlib import Path
from .publication import PublicationScope, index_lines, local_references
from .articles import read_article, headings as _headings, slugify as _slug

CANDIDATE_MAX_BYTES = 2048
RESPONSE_MAX_BYTES = 16384
QUERY_MAX_BYTES = 512
FIELDS = ("index_results", "article_results", "fulltext_results")


def json_bytes(value) -> int:
    """Budget the providers' ensure_ascii=False JSON (default separators)."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _budget_bytes(value) -> int:
    # FastMCP renders TextContent with indent=2; Hermes uses default separators.
    return max(
        json_bytes(value),
        len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")),
    )


def current_entry(path: Path, cached: dict) -> tuple[int, str] | None:
    """Resolve surviving article claims, INDEX lines or glossary headings.

    Changed entries are invalidated; live grep can discover their new wording.
    A moved but unchanged entry is relocated by content, not its old line number.
    """
    if cached.get("kind") == "article-claim" and path.parent.parent.name == "articles" and path.name not in {"INDEX.md", "CONTEXT.md"}:
        return next(((c.line, c.text) for c in read_article(path).claims
                     if c.text == cached.get("text")), None)
    if path.name.casefold() not in {"index.md", "context.md"}:
        return None
    text = str(cached.get("text", "")).strip()
    if not text:
        return None
    expected = text.splitlines()[0] if path.name.casefold() == "context.md" else text
    for number, line in index_lines(path):
        if line.strip() == expected:
            return number, line.strip()
    return None


def current_locator(
    path: Path,
    *,
    scope: PublicationScope,
    line: int | None = None,
    section: str | None = None,
    claim_text: str | None = None,
) -> dict:
    headings = _headings(path)
    title = next((text for _, level, text in headings if level == 1), path.stem)
    result = {"title": title, "section": "", "anchor": ""}
    if section:
        selected = next((h for h in headings if h[2] == section), None)
        if selected is None:
            result["omitted"] = ["section:stale_index"]
    else:
        selected = next((h for h in reversed(headings) if line and h[0] <= line), None)
    if selected:
        result.update(section=selected[2], anchor=_slug(selected[2]), line=selected[0])
    if line:
        result["line"] = line
    claims = []
    if path.name.casefold() == "index.md":
        claims = [
            (path, n, text) for n, text in index_lines(path)
            if n == line and text.lstrip().startswith("- ") and scope.allows_line(path, text)
        ]
    elif scope.allows_article(path):
        article = read_article(path)
        claims = [(path, c.line, c.text) for c in article.claims]
        # Prefer the matched entry, then a self-anchor for the hit section,
        # otherwise preserve the authored array order.
        claims.sort(key=lambda c: (
            c[2] != claim_text if claim_text is not None else False,
            not any(t == path.resolve() and a == result["anchor"] and a
                    for t, a in local_references(path, c[2])),
        ))
        if article.error:
            result.setdefault("omitted", []).append("claim:invalid")
    if claims:
        index, number, text = claims[0]
        result["claim"] = text if scope.allows_article(path) else text.strip()
        result["claim_source"] = {"path": str(index), "line": number}
        result["links"] = [
            {"path": str(target), "anchor": anchor}
            for target, anchor in local_references(index, text)
            if scope.allows(target)
        ]
    else:
        result.setdefault("omitted", []).append("claim:not_authored")
    return result


def bound_response(response: dict, *, query_omitted: bool = False) -> dict:
    response["limits"] = {
        "candidate_bytes": CANDIDATE_MAX_BYTES,
        "response_bytes": RESPONSE_MAX_BYTES,
        "query_bytes": QUERY_MAX_BYTES,
        "encoding": "UTF-8 JSON; ensure_ascii=false; default separators or indent=2",
    }
    response["omitted"] = {"candidates": 0, "fields": 0, "query": query_omitted}
    fields = [field for field in FIELDS if field in response]
    for field in fields:
        kept = []
        for row in response[field]:
            for key in (
                "claim",
                "links",
                "title",
                "section",
                "anchor",
                "claim_source",
                "package",
            ):
                if _budget_bytes(row) <= CANDIDATE_MAX_BYTES:
                    break
                if key in row:
                    del row[key]
                    row.setdefault("omitted", []).append(f"{key}:byte_limit")
                    response["omitted"]["fields"] += 1
            if _budget_bytes(row) > CANDIDATE_MAX_BYTES:
                response["omitted"]["candidates"] += 1
            else:
                kept.append(row)
        response[field] = kept
    # Retain engine order within each field; no invented cross-field ranking.
    for field in reversed(fields):
        while response[field] and _budget_bytes(response) > RESPONSE_MAX_BYTES:
            response[field].pop()
            response["omitted"]["candidates"] += 1
    if any(response["omitted"].values()):
        response["truncated"] = True
    return response
