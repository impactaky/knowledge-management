"""Explicit sidecar access, separate from the published knowledge corpus."""
from pathlib import Path

from .core import parse_catalog, resolve_catalog_path


def comment_path(path: str | Path, *, catalog_path=None) -> Path:
    """Resolve an existing Markdown article and its local, non-symlink sidecar."""
    source = Path(path)
    if not source.is_absolute() or source.suffix.lower() != ".md":
        raise ValueError("Comments require an absolute .md article path")
    catalog = resolve_catalog_path(catalog_path)
    roots = [p.root for p in parse_catalog(catalog, strict=True)]
    roots.append((catalog.parent / "drafts").resolve())
    article = source.resolve()
    if not any(article.is_relative_to(root) for root in roots):
        raise ValueError("Article is outside Catalog package and drafts roots")
    if article.suffix.lower() != ".md":
        raise ValueError("Resolved article must be a .md file")
    if not article.is_file():
        raise ValueError("Article does not exist or is not a file")
    if ".comments" in article.parts:
        raise ValueError("A comment sidecar is not an article")
    target = article.parent / ".comments" / f"{article.stem}.md"
    # Do not redirect comment reads/writes, even to a different in-scope article.
    if target.parent.is_symlink() or target.is_symlink():
        raise ValueError("Comment directories and files must not be symlinks")
    if target.exists() and not target.is_file():
        raise ValueError("Comment path is not a file")
    return target


def comments(path: str | Path, *, catalog_path=None) -> dict:
    """Return raw Markdown, including unrecognized records; missing is explicit."""
    target = comment_path(path, catalog_path=catalog_path)
    try:
        content = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"path": str(target), "exists": False, "content": ""}
    return {"path": str(target), "exists": True, "content": content}
