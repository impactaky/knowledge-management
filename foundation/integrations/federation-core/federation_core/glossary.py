"""Resolve search entries to complete, current CONTEXT glossary blocks."""

from pathlib import Path
import re

from .articles import prose_lines


TERM_RE = re.compile(r"^\*\*([^*]+)\*\*:")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
SETEXT_RE = re.compile(r"^\s{0,3}(?:=+|-+)\s*$")


def current_context_entry(path: Path, cached: dict) -> tuple[int, str, str | None] | None:
    """Return (source line, identity text, definition) for a search hit.

    Cached term blocks resolve by the authored term name, even if an inline
    definition changed. Live paragraph hits resolve by their current position.
    Fenced examples cannot start a term or supply a matching prose line.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    prose = list(prose_lines(enumerate(lines, start=1)))
    # Markdown section headings end a glossary block at any level. A setext
    # heading starts at its title, not its underline.
    boundaries = {}
    for i, (number, text) in enumerate(prose):
        term = TERM_RE.match(text.strip())
        if term:
            boundaries[number] = term[1].strip()
        elif HEADING_RE.match(text):
            boundaries[number] = None
        elif SETEXT_RE.match(text) and i and prose[i - 1][0] == number - 1:
            previous = prose[i - 1][1].strip()
            if previous and not TERM_RE.match(previous):
                boundaries[number - 1] = None
    starts = sorted(boundaries)
    blocks = [
        (start, starts[i + 1] if i + 1 < len(starts) else len(lines) + 1, boundaries[start])
        for i, start in enumerate(starts) if boundaries[start] is not None
    ]

    expected = str(cached.get("text", "")).strip()
    if not expected:
        return None
    expected = expected.splitlines()[0]
    term = TERM_RE.match(expected)
    if term:
        matches = [block for block in blocks if block[2] == term[1].strip()]
    elif cached.get("kind") == "term":
        return None
    else:
        matches = [(number, text) for number, text in prose if text.strip() == expected]
        if not matches:
            return None
        number, text = next((item for item in matches if item[0] == cached.get("line")), matches[0])
        matches = [block for block in blocks if block[0] <= number < block[1]]
        if not matches:
            return number, text.strip(), None
    if not matches:
        return None
    start, end, _ = next((block for block in matches if block[0] == cached.get("line")), matches[0])
    definition = "\n".join(lines[start - 1:end - 1]).strip()
    return start, lines[start - 1].strip(), definition
