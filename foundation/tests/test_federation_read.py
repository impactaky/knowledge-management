"""Publication-scoped reads using only temporary synthetic files."""
from pathlib import Path

import pytest

from federation_core import read


@pytest.fixture
def store(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    catalog = tmp_path / "CATALOG.md"
    catalog.write_text("## Packages\n- [fixture](package/) — Synthetic package\n")
    return catalog, package


def test_read_published_utf8(store):
    catalog, package = store
    path = package / "guide.md"
    path.write_text("# Guide\n合成 fixture\n", encoding="utf-8")
    assert read(path, catalog_path=catalog) == {"path": str(path), "content": path.read_text()}


@pytest.mark.parametrize("directory", ["drafts", "tests", "worklogs", ".hidden", "assets"])
def test_read_rejects_excluded_placement(store, directory):
    catalog, package = store
    path = package / directory / "guide.md"
    path.parent.mkdir()
    path.write_text("synthetic excluded text")
    # An explicitly nested Catalog entry cannot bypass the outer boundary.
    catalog.write_text(catalog.read_text() + f"- [nested](package/{directory}/) — Nested fixture\n")
    with pytest.raises(ValueError, match="publication scope"):
        read(path, catalog_path=catalog)


@pytest.mark.parametrize("kind", ["outside", "symlink", "draft_symlink", "missing", "directory", "large", "binary", "invalid_utf8", "relative"])
def test_read_rejects_unsafe_files(store, kind):
    catalog, package = store
    path = package / "guide.md"
    if kind in {"outside", "symlink"}:
        outside = catalog.parent / "outside.md"
        outside.write_text("synthetic outside text")
        if kind == "symlink":
            path.symlink_to(outside)
        else:
            path = outside
    elif kind == "draft_symlink":
        draft = package / "drafts" / "draft.md"
        draft.parent.mkdir()
        draft.write_text("synthetic draft")
        path.symlink_to(draft)
    elif kind == "directory":
        path.mkdir()
    elif kind == "large":
        path.write_bytes(b"x" * 2_000_001)
    elif kind == "binary":
        path.write_bytes(b"text\x00binary")
    elif kind == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif kind == "relative":
        path = Path("guide.md")
    with pytest.raises(ValueError):
        read(path, catalog_path=catalog)
