"""Shared synthetic fixtures for federation and knowledge-ui tests.

Importing this module keeps test modules independent of each other. The
helpers never read a live catalog, backend or the developer's store.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch

FOUNDATION = Path(__file__).resolve().parents[1]
ROOT = FOUNDATION.parent
SAMPLE_CATALOG = ROOT / "examples" / "minimal" / "CATALOG.md"
UI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "knowledge-ui"

ARTICLE_TEXT = (
    '---\nsource: "ISBN:example ch.2"\nclaims:\n'
    '  - "- First — [one](article.md#one)"\n'
    '  - "- Second — [two](article.md#two)"\n'
    '---\n# Article\n\n## One\nBODY_ONLY\n\n## Two\nOTHER_BODY\n'
)


class ArticleStoreFixture:
    """Mixin building a self-contained article repository in a temporary store."""

    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"MEILI_URL": "http://fixture.invalid"}))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "external"
        self.repo.mkdir()
        self.catalog = self.write("CATALOG.md", "## パッケージ\n- [arbitrary-label](INDEX.md) — Package\n")
        self.write("INDEX.md", "# Package\n")
        self.path = self.write("articles/theme/article.md", ARTICLE_TEXT)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


def make_fixture(root: Path) -> tuple[Path, Path]:
    """Create a self-contained glossary, article and catalog store."""
    package = root / "articles" / "theme"
    package.mkdir(parents=True)
    (package / "INDEX.md").write_text(
        "# Package\n\n- specification claim — [article](article.md)\n",
        encoding="utf-8",
    )
    (package / "CONTEXT.md").write_text(
        "# Context\n\n**Term**: high-signal term definition\n",
        encoding="utf-8",
    )
    (package / "article.md").write_text(
        '---\nclaims: ["- Authored claim — [article](article.md)"]\n---\n'
        "# Article\n\n## First\nneedle in first chunk\n\n"
        "## Best\nneedle in best chunk\n",
        encoding="utf-8",
    )
    catalog = root / "CATALOG.md"
    catalog.write_text(
        "# CATALOG\n\n## パッケージ\n\n"
        "- [fixture](articles/theme/INDEX.md) — Fixture package\n",
        encoding="utf-8",
    )
    return catalog, package


def load_hermes_provider():
    """Load the Hermes federation provider with a stubbed native memory_provider."""
    if "agent" not in sys.modules:
        agent = ModuleType("agent")
        sys.modules["agent"] = agent
    if "agent.memory_provider" not in sys.modules:
        memory_provider = ModuleType("agent.memory_provider")

        class MemoryProvider:
            pass

        memory_provider.MemoryProvider = MemoryProvider
        sys.modules["agent.memory_provider"] = memory_provider
        sys.modules["agent"].memory_provider = memory_provider

    module_path = FOUNDATION / "integrations" / "hermes-federation" / "__init__.py"
    spec = importlib.util.spec_from_file_location("hermes_federation_provider", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
