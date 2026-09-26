"""Read-only checks for the catalog, published knowledge and article links."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re

import indexer
from federation_core.articles import read_article, prose_lines, anchors
from federation_core import parse_catalog
from federation_core.core import resolve_catalog_path
from federation_core.publication import PublicationScope, index_lines, local_references


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    code: str
    detail: str


def check(catalog_path: Path, *, local_only: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    try:
        parse_catalog(catalog_path, strict=True)
        packages = indexer.parse_catalog(catalog_path)
        if local_only:
            packages = [p for p in packages if p["root"].is_relative_to(catalog_path.resolve().parent)]
    except (OSError, ValueError) as exc:
        return [Finding(catalog_path, 1, "catalog", str(exc))]

    anchor_cache: dict[Path, set[str]] = {}
    for package in packages:
        root = package["root"]
        entry = package["entry_path"]
        is_directory = entry == root
        if not (entry.is_dir() if is_directory else entry.is_file()):
            kind = "directory" if is_directory else "file"
            findings.append(Finding(entry, 1, "missing-entry", f"Catalog entry is not an existing {kind}"))
        # Validate the package scope independently of its entrance.
        scope = PublicationScope(root, boundary=package.get("publication_root"))
        definitions: dict[tuple[Path, str], tuple[Path, int]] = {}
        for doc in indexer.collect_term_entries(package):
            source = Path(doc["file"])
            if not indexer.package_owns_file(package, source, packages):
                continue
            term = re.match(r"\*\*([^*]+)\*\*:", doc["text"]).group(1).casefold()
            # A shared root/modules glossary is one vocabulary. Other CONTEXT
            # files (e.g. unrelated article themes) remain separate models;
            # sharing a Package alone does not make them one Bounded Context.
            relative = source.relative_to(root)
            group = (
                root / "CONTEXT.md"
                if relative.parts[0] == "modules" and (root / "CONTEXT.md").is_file()
                else source
            )
            key = (group, term)
            if key in definitions:
                previous, line = definitions[key]
                findings.append(Finding(source, doc["line"], "duplicate-term", f"{term}; also defined at {previous}:{line}"))
            else:
                definitions[key] = (source, doc["line"])

        sources = set(root.rglob("*.md")) | scope.article_publication[1]
        for source in sorted(sources):
            if not scope.allows(source) or not indexer.package_owns_file(package, source, packages):
                continue
            if scope.allows_article(source):
                article = read_article(source)
                if article.error or not article.claims:
                    findings.append(Finding(source, 1, "invalid-claims" if article.error else "missing-claims",
                                            article.error or "Article has no authored claims"))
                lines = [*prose_lines(article.body_lines), *((c.line, c.text) for c in article.claims)]
            else:
                lines = index_lines(source)
            for line_number, line in lines:
                for target, fragment in local_references(source, line):
                    if not target.exists():
                        findings.append(Finding(source, line_number, "missing-link", str(target)))
                    elif local_only and not target.is_relative_to(catalog_path.resolve().parent):
                        continue
                    elif fragment and target.is_file() and target.suffix in {".md", ".py"}:
                        if target not in anchor_cache:
                            anchor_cache[target] = anchors(target)
                        if fragment not in anchor_cache[target]:
                            findings.append(Finding(source, line_number, "missing-anchor", f"{target}#{fragment}"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--local-only", action="store_true", help="Check only packages and anchor targets inside the Catalog directory; do not read external stores")
    args = parser.parse_args()
    if args.local_only:
        print("Scope: local packages and local anchors only; external stores not read")
    catalog_path = resolve_catalog_path(args.catalog)
    checkout = Path(__file__).resolve().parents[3]
    if not catalog_path.resolve().is_relative_to(checkout):
        try:
            packages = indexer.parse_catalog(catalog_path)
        except (OSError, ValueError):
            packages = []
        if not any(p["root"].resolve() == checkout / "foundation" for p in packages):
            print("Advisory: missing shared-rules entry for this checkout; add to Catalog Packages:")
            print(f"- [shared-rules]({checkout / 'foundation/INDEX.md'}) — Shared rules")
            print("See shared-rules の docs/store-changes.md for store-side changes.")
    findings = check(catalog_path, local_only=args.local_only)
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.code}: {finding.detail}")
    print(f"Knowledge check: {len(findings)} issue(s)")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
