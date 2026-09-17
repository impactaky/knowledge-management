"""Placement-based search publication shared by the indexer and live search."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterator
from urllib.parse import unquote, urlsplit
from .articles import markdown_nodes, prose_lines


EXCLUDED_DIRS = {
    "__pycache__", "__marimo__", "node_modules",
    "drafts", "tests", "test", "fixtures", "worklogs",
    "experiments", "evaluations", "books", "studies", "assets",
}
ARTICLE_WORK_DIRS = {"books", "studies"}
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)", re.DOTALL)


def local_references(source: Path, text: str) -> list[tuple[Path, str]]:
    references = []
    for match in LINK_RE.finditer(INLINE_CODE_RE.sub("", text)):
        try:
            target = urlsplit(match.group(1).strip("<>"))
        except ValueError:
            continue
        if target.scheme or target.netloc:
            continue
        path = (source.parent / unquote(target.path)).resolve() if target.path else source.resolve()
        references.append((path, unquote(target.fragment)))
    return references


def local_links(source: Path, text: str) -> list[Path]:
    return [path for path, _ in local_references(source, text)]


def index_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield numbered prose lines, ignoring fenced examples of claim lines."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    yield from prose_lines(enumerate(lines, start=1))


class PublicationScope:
    def __init__(self, root: Path, *, boundary: Path | None = None):
        self.root = root.resolve()
        self.boundary = (boundary or root).resolve()
        # The article-layer layout is shared across stores; Catalog display
        # names have no bearing on publication. A catalog may also point
        # directly to an article layer or one of its flat themes.
        self.article_root = next(
            (p for p in (self.root, *self.root.parents) if p.name == "articles"),
            self.root / "articles",
        )

    def _excluded(self, path: Path) -> bool:
        if self.root.name in EXCLUDED_DIRS or self.root.name.startswith('.'):
            return True
        try:
            parts = path.relative_to(self.boundary).parts
        except ValueError:
            return True
        if path.name == "regression-queries.json":
            return True
        if any(part.startswith(".") or part in EXCLUDED_DIRS for part in parts):
            return True
        if path.is_relative_to(self.article_root):
            return any(part in ARTICLE_WORK_DIRS for part in path.relative_to(self.article_root).parts)
        return False

    @property
    def article_publication(self) -> tuple[set[Path], set[Path]]:
        """Current flat article files; claims are not a publication gate."""
        articles = {
            p.resolve() for p in self.article_root.glob("*/*")
            if self.allows_article(p)
        }
        indexes = {p for p in self.root.rglob("INDEX.md") if self.allows(p)}
        return indexes, articles

    def _safe_file(self, path: Path) -> bool:
        lexical = path.absolute()
        resolved = path.resolve()
        return (
            lexical.is_relative_to(self.root) and resolved.is_relative_to(self.root)
            and not self._excluded(lexical) and not self._excluded(resolved)
            and resolved.is_file()
        )

    def allows(self, path: Path) -> bool:
        # Validate both lexical and resolved placement at every lookup. A stale
        # hit or symlink must not expose a draft, asset or outside file.
        if not self._safe_file(path):
            return False
        resolved = path.resolve()
        if resolved.is_relative_to(self.article_root):
            if resolved.name == "CONTEXT.md":
                return len(resolved.relative_to(self.article_root).parts) <= 2
            if resolved.name == "INDEX.md":
                return resolved.parent in {self.article_root, self.root}
            return self.allows_article(path)
        return True

    def allows_line(self, path: Path, text: str) -> bool:
        """Do not publish INDEX navigation to excluded work as a claim."""
        if path.name.casefold() != "index.md":
            return True
        for target in local_links(path, text):
            if not target.is_relative_to(self.boundary):
                continue
            if self._excluded(target):
                return False
            if target.is_relative_to(self.article_root) and target.is_file() and not self.allows(target):
                return False
        return True

    def allows_article(self, path: Path) -> bool:
        if not self._safe_file(path):
            return False
        resolved = path.resolve()
        if resolved.suffix.lower() == ".py":
            try:
                if not markdown_nodes(resolved.read_text(encoding="utf-8")):
                    return False
            except (OSError, UnicodeError, SyntaxError):
                return False
        return (
            resolved.parent.parent == self.article_root
            and path.absolute().parent.parent == self.article_root
            and resolved.name not in {"INDEX.md", "CONTEXT.md"}
            and resolved.suffix.lower() in {".md", ".py"}
        )


def publication_boundary(root: Path, roots: list[Path], *, catalog: Path | None = None) -> Path:
    """Nested Catalog roots cannot re-publish work excluded by an outer root."""
    if catalog is not None and root.is_relative_to(catalog.resolve().parent):
        return catalog.resolve().parent
    return min((p for p in roots if root.is_relative_to(p)), key=lambda p: len(p.parts))
