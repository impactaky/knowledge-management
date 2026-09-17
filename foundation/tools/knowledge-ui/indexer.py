"""
知識連合インデクサ

CATALOG.md をパースしてパッケージルート一覧を導出し、
Meilisearch の entries / chunks インデックスを構築する。

Usage:
    uv run python indexer.py [--catalog PATH] [--meili-url URL]
"""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
import fcntl
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

import meilisearch

CORE_PATH = Path(__file__).resolve().parents[2] / "integrations" / "federation-core"
if str(CORE_PATH) not in sys.path:
    sys.path.insert(0, str(CORE_PATH))

from federation_core.articles import (read_article, parse_markdown_metadata, extract_marimo_markdown,
                                      extract_marimo_markdown_cells, slugify)
from federation_core.publication import PublicationScope, index_lines, publication_boundary  # noqa: E402
from federation_core import parse_catalog as read_catalog_packages  # noqa: E402

from federation_core.core import resolve_catalog_path

DEFAULT_CATALOG = None
DEFAULT_MEILI_URL = ""
# Conservative byte budget for the complete title/section/body embedding
# input. All body text is retained across fragments; nothing is truncated.
EMBEDDING_INPUT_MAX_BYTES = 3000
INDEX_TASK_TIMEOUT_MS = 600_000


class IndexingBusy(RuntimeError):
    pass


@contextmanager
def indexing_lock(meili_url: str, index_prefix: str):
    key = hashlib.sha256(f"{meili_url.rstrip('/')}:{index_prefix}".encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / f"knowledge-index-{os.getuid()}-{key}.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IndexingBusy("An index refresh is already running for this destination") from exc
        yield
    finally:
        os.close(descriptor)


def corpus_fingerprint(catalog_path: Path) -> str:
    """Track source membership and revisions, including claim withdrawals."""
    fingerprint = hashlib.sha256()
    catalog_stat = catalog_path.stat()
    fingerprint.update(f"{catalog_path.resolve()}:{catalog_stat.st_mtime_ns}:{catalog_stat.st_size}".encode())
    files = set()
    for package in parse_catalog(catalog_path):
        root = package["root"]
        entry = package["entry_path"]
        # Track file entrances as well as searchable sources; deleted entrances
        # must trigger validation even if they supplied no index documents.
        fingerprint.update(f"{entry}:{entry.is_dir()}:{entry.is_file()}".encode())
        if entry.is_file():
            files.add(entry)
        scope = PublicationScope(root, boundary=package.get("publication_root"))
        files.update(scope.article_publication[1])
        files.update(p for p in root.rglob("INDEX.md") if scope.allows(p))
        files.update(p for p in root.rglob("CONTEXT.md") if scope.allows(p))
    for path in sorted(files):
        stat = path.stat()
        fingerprint.update(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    return fingerprint.hexdigest()


def wait_for_tasks(client: meilisearch.Client, tasks: list) -> None:
    for task in tasks:
        result = client.wait_for_task(task.task_uid, timeout_in_ms=INDEX_TASK_TIMEOUT_MS, interval_in_ms=250)
        if result.status != "succeeded":
            raise RuntimeError(f"Index task {task.task_uid} {result.status}: {result.error}")


# ---------------------------------------------------------------------------
# CATALOG パーサ
# ---------------------------------------------------------------------------

def parse_catalog(catalog_path: Path) -> list[dict]:
    """Adapt the shared catalog parser to the indexer's Path-valued records."""
    packages = read_catalog_packages(catalog_path, strict=True)
    return [
        {"name": p.name, "description": p.description, "entry_path": p.entry_path, "root": p.root,
         "publication_root": publication_boundary(p.root, [item.root for item in packages], catalog=catalog_path)}
        for p in packages
    ]


def package_owns_file(package: dict, file_path: str | Path, packages: list[dict]) -> bool:
    """Assign overlapping catalog roots to the most-specific package."""
    path = Path(file_path).resolve()
    owners = []
    for candidate in packages:
        root = Path(candidate["root"]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        owners.append(candidate)
    if not owners:
        return False
    owner = max(owners, key=lambda candidate: len(Path(candidate["root"]).resolve().parts))
    return Path(owner["root"]).resolve() == Path(package["root"]).resolve()


# ---------------------------------------------------------------------------
# ID 正規化
# ---------------------------------------------------------------------------

def make_id(*parts: str) -> str:
    """ファイルパス + 連番等からドキュメントIDを生成。英数字・ハイフン・アンダースコアのみ。"""
    raw = "_".join(str(p) for p in parts)
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "_", raw)
    # 長すぎる場合は末尾をハッシュで短縮
    if len(safe) > 200:
        h = hashlib.sha1(raw.encode()).hexdigest()[:8]
        safe = safe[:180] + "_" + h
    return safe


# ---------------------------------------------------------------------------
# entries インデックス用パーサ
# ---------------------------------------------------------------------------

def collect_index_entries(package: dict) -> list[dict]:
    """
    パッケージルート配下の INDEX.md を再帰的に集め、
    '- ' で始まる行を 1行=1ドキュメントとして返す。
    """
    root: Path = package["root"]
    scope = PublicationScope(root, boundary=package.get("publication_root"))
    docs = []
    for index_file in root.rglob("INDEX.md"):
        if not scope.allows(index_file):
            continue
        for lineno, line in index_lines(index_file):
            stripped = line.strip()
            if stripped.startswith("- ") and scope.allows_line(index_file, line):
                doc_id = make_id(str(index_file), str(lineno))
                docs.append(
                    {
                        "id": doc_id,
                        "kind": "index-entry",
                        "text": stripped,
                        "package": package["name"],
                        "file": str(index_file),
                        "rel": str(index_file.relative_to(root)),
                        "line": lineno,
                        "end_line": lineno,
                    }
                )
    return docs


def collect_claim_entries(package: dict) -> list[dict]:
    root = package["root"]
    scope = PublicationScope(root, boundary=package.get("publication_root"))
    return [
        {"id": make_id(str(path), "claim", str(claim.index)), "kind": "article-claim",
         "text": claim.text, "package": package["name"], "file": str(path),
         "rel": str(path.relative_to(root)), "line": claim.line, "end_line": claim.line}
        for path in sorted(scope.article_publication[1]) for claim in read_article(path).claims
    ]


def collect_term_entries(package: dict) -> list[dict]:
    """
    パッケージルート配下の CONTEXT.md を再帰的に集め、
    '**用語 (English)**:' 形式の用語ブロック(見出し+定義段落)を
    1ブロック=1ドキュメントとして返す。
    """
    root: Path = package["root"]
    scope = PublicationScope(root, boundary=package.get("publication_root"))
    docs = []
    # **term (...)**: or **term**:
    heading_re = re.compile(r"^\*\*([^*]+)\*\*:")

    for ctx_file in root.rglob("CONTEXT.md"):
        if not scope.allows(ctx_file):
            continue
        lines = ctx_file.read_text(encoding="utf-8", errors="replace").splitlines()
        i = 0
        block_index = 0
        while i < len(lines):
            line = lines[i]
            m = heading_re.match(line.strip())
            if m:
                heading = m.group(1).strip()
                # 次の空行または次の **term**: まで本文を収集
                body_lines = [line.strip()]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if heading_re.match(next_line) or next_line.startswith("## ") or next_line.startswith("### "):
                        break
                    body_lines.append(next_line)
                    j += 1
                text = "\n".join(body_lines).strip()
                doc_id = make_id(str(ctx_file), str(block_index))
                docs.append(
                    {
                        "id": doc_id,
                        "kind": "term",
                        "text": text,
                        "package": package["name"],
                        "file": str(ctx_file),
                        "rel": str(ctx_file.relative_to(root)),
                        "line": i + 1,
                        "end_line": j,
                    }
                )
                block_index += 1
                i = j
            else:
                i += 1

    return docs


# ---------------------------------------------------------------------------
# chunks インデックス用パーサ (各Packageの記事層)
# ---------------------------------------------------------------------------

def extract_markdown_title(markdown: str, fallback: str) -> str:
    """front matter title、なければ最初のH1、なければfallbackを返す。"""
    metadata = parse_markdown_metadata(markdown)
    if isinstance(metadata.get("title"), str) and metadata["title"].strip():
        return metadata["title"]
    for line in markdown.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            if title:
                return title
    return fallback


def source_section_range(article_file: Path, heading: str) -> tuple[int, int] | None:
    """Locate an indexed prose section in its source file for backend dedupe."""
    try:
        lines = article_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    if heading:
        expected = f"## {heading}".casefold()
        start_index = next(
            (index for index, line in enumerate(lines) if line.strip().casefold() == expected),
            None,
        )
        if start_index is None:
            return None
    else:
        start_index = 0
    end_index = next(
        (
            index - 1
            for index in range(start_index + 1, len(lines))
            if lines[index].strip().startswith("## ")
        ),
        len(lines) - 1,
    )
    return start_index + 1, end_index + 1


def split_embedding_text(text: str, budget: int) -> list[str]:
    """Prefer line boundaries, splitting long lines at UTF-8 boundaries."""
    if budget < 4:
        raise ValueError("Article title and section leave no room for embedding text")
    fragments = []
    remaining = text
    while len(remaining.encode("utf-8")) > budget:
        prefix = remaining.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
        newline = prefix.rfind("\n")
        if newline >= len(prefix) // 2:
            prefix = prefix[:newline + 1]
        fragments.append(prefix)
        remaining = remaining[len(prefix):]
    if remaining:
        fragments.append(remaining)
    return fragments


def collect_article_chunks(package: dict) -> list[dict]:
    """
    記事層のテーマ直下の *.md と静的marimo *.py を
    ## 見出しセクション単位で分割する。*.py は mo.md セルのみ対象。
    """
    root: Path = package["root"]
    docs = []

    scope = PublicationScope(root, boundary=package.get("publication_root"))
    article_files = sorted(scope.article_publication[1])

    for article_file in article_files:
        article = read_article(article_file)
        content = article.body
        article_type = "executable" if article_file.suffix.lower() == ".py" else "markdown"
        if not content.strip():
            continue
        lines = content.splitlines()
        title = extract_markdown_title(article.markdown, article_file.stem)
        first_body_line = article.body_lines[0][0] if article.body_lines else 1

        # ## セクション分割
        sections: list[tuple[str, list[str], int]] = []  # (heading, lines, body line)
        current_heading = ""
        current_lines: list[str] = []
        body_line = first_body_line

        for line_number, line in enumerate(lines, start=first_body_line):
            if line.startswith("## "):
                if current_lines or current_heading:
                    sections.append((current_heading, current_lines, body_line))
                current_heading = line[3:].strip()
                current_lines = []
                body_line = line_number + 1
            else:
                current_lines.append(line)

        if current_lines or current_heading:
            sections.append((current_heading, current_lines, body_line))

        for idx, (heading, sec_lines, first_line) in enumerate(sections):
            raw_text = "\n".join(sec_lines)
            text = raw_text.strip()
            if not text and not heading:
                continue
            anchor = slugify(heading) if heading else ""
            budget = EMBEDDING_INPUT_MAX_BYTES - len(f"{title}\n{heading}\n".encode("utf-8"))
            fragments = split_embedding_text(text or heading, budget)
            line_cursor = first_line + raw_text[:len(raw_text) - len(raw_text.lstrip())].count("\n")
            for part, fragment in enumerate(fragments):
                doc_id = (
                    make_id(str(article_file), str(idx)) if len(fragments) == 1
                    else make_id(str(article_file), str(idx), str(part))
                )
                doc = {
                    "id": doc_id,
                    "text": fragment,
                    "package": package["name"],
                    "title": title,
                    "section": heading,
                    "file": str(article_file.resolve()),
                    "anchor": anchor,
                    "article_type": article_type,
                }
                if len(fragments) == 1:
                    source_range = source_section_range(article_file, heading)
                    if source_range is not None:
                        doc["start_line"], doc["end_line"] = source_range
                elif article_type == "markdown":
                    doc["start_line"] = line_cursor
                    doc["end_line"] = line_cursor + fragment.count("\n") - int(fragment.endswith("\n"))
                # Extracted marimo cells need not be contiguous source lines.
                # Omit ranges for their fragments instead of suppressing grep
                # matches at guessed Python source positions.
                line_cursor += fragment.count("\n")
                docs.append(doc)

    return docs


def collect_catalog_article_chunks(packages: list[dict]) -> list[dict]:
    """Collect every catalog owner's published articles into one index."""
    return [
        doc
        for package in packages
        for doc in collect_article_chunks(package)
        if package_owns_file(package, doc["file"], packages)
    ]


# ---------------------------------------------------------------------------
# Meilisearch セットアップ
# ---------------------------------------------------------------------------

def reconcile_deletions(client: meilisearch.Client, index, new_ids: set[str]) -> None:
    """
    今回の生成集合に無いIDのドキュメントをインデックスから削除する。

    swap再構築ではなくupsert+突き合わせ削除なのは意図的: 新インデックスは
    embedding キャッシュを持たないため、swapだと未変更チャンクまで毎回
    全量再embeddingになる。upsertならMeilisearchがdocumentTemplate出力の
    変わらないドキュメントのembeddingをスキップする。
    """
    stale: list[str] = []
    offset = 0
    try:
        while True:
            batch = index.get_documents({"fields": ["id"], "limit": 1000, "offset": offset})
            if not batch.results:
                break
            stale.extend(d.id for d in batch.results if d.id not in new_ids)
            offset += len(batch.results)
            if offset >= batch.total:
                break
    except meilisearch.errors.MeilisearchApiError as exc:
        if exc.code == "index_not_found":
            return
        raise
    if stale:
        task = index.delete_documents(stale)
        print(f"[{index.uid}] deleting {len(stale)} stale documents (task {task.task_uid})")
        # Finish withdrawal before enqueueing additions. Meilisearch can batch
        # them together; one failed embedding must not roll back the deletions.
        wait_for_tasks(client, [task])


def setup_entries_index(client: meilisearch.Client, docs: list[dict], index_name: str = "entries") -> None:
    index = client.index(index_name)
    reconcile_deletions(client, index, {doc["id"] for doc in docs})

    # 設定
    tasks = [
        index.update_searchable_attributes(["text"]),
        index.update_typo_tolerance({"enabled": True}),
        index.update_displayed_attributes(
            ["id", "kind", "text", "package", "file", "rel", "line", "end_line"]
        ),
        index.update_filterable_attributes(["kind", "package"]),
    ]
    wait_for_tasks(client, tasks)

    if docs:
        task = index.add_documents(docs, primary_key="id")
        wait_for_tasks(client, [task])
    print(f"[{index_name}] indexed {len(docs)} documents")


def setup_chunks_index(client: meilisearch.Client, docs: list[dict], index_name: str = "chunks") -> bool:
    """
    Returns True if embedder was configured successfully.
    """
    index = client.index(index_name)
    reconcile_deletions(client, index, {d["id"] for d in docs})

    tasks = [index.update_searchable_attributes(["text", "title", "section"])]
    tasks.append(index.update_displayed_attributes(
        [
            "id",
            "text",
            "package",
            "title",
            "section",
            "file",
            "anchor",
            "article_type",
            "start_line",
            "end_line",
        ]
    ))
    tasks.append(index.update_filterable_attributes([]))

    if os.environ.get("OLLAMA_EMBED_URL"):
        embedder_settings = {
            "default": {
                "source": "ollama",
                "url": os.environ["OLLAMA_EMBED_URL"],
                "model": os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3"),
                "documentTemplate": "{{doc.title}}\n{{doc.section}}\n{{doc.text}}",
            }
        }
        tasks.append(index.update_embedders(embedder_settings))
    wait_for_tasks(client, tasks)

    if docs:
        task = index.add_documents(docs, primary_key="id")
        wait_for_tasks(client, [task])
    print(f"[{index_name}] indexed {len(docs)} documents")

    return bool(os.environ.get("OLLAMA_EMBED_URL"))


# ---------------------------------------------------------------------------
# メインエントリ
# ---------------------------------------------------------------------------

def main(catalog_path: Path, meili_url: str, index_prefix: str = "") -> None:
    if not meili_url:
        raise ValueError("Set MEILI_URL or pass --meili-url; use --dry-run for offline documents")
    with indexing_lock(meili_url, index_prefix):
        _index_catalog(catalog_path, meili_url, index_prefix)


def _index_catalog(catalog_path: Path, meili_url: str, index_prefix: str) -> None:
    # A partially written or ambiguous catalog must not erase derived data.
    for package in read_catalog_packages(catalog_path, strict=True):
        entry = package.entry_path
        is_directory = entry == package.root
        if not (entry.is_dir() if is_directory else entry.is_file()):
            raise ValueError(f"Catalog entry is not an existing {'directory' if is_directory else 'file'}: {entry}")
    before = corpus_fingerprint(catalog_path)
    print(f"Catalog: {catalog_path}")
    packages = parse_catalog(catalog_path)
    print(f"Found {len(packages)} packages: {[p['name'] for p in packages]}")

    client = meilisearch.Client(meili_url, os.environ.get("MEILI_API_KEY"))

    entries_index = f"{index_prefix}entries" if index_prefix else "entries"
    chunks_index = f"{index_prefix}chunks" if index_prefix else "chunks"

    # entries: Package INDEX / CONTEXT and article-owned claims
    entry_docs: list[dict] = []
    for pkg in packages:
        if not pkg["root"].exists():
            print(f"  [skip] {pkg['name']}: root {pkg['root']} not found")
            continue
        idx_entries = [
            doc for doc in collect_index_entries(pkg)
            if package_owns_file(pkg, doc["file"], packages)
        ]
        term_entries = [
            doc for doc in collect_term_entries(pkg)
            if package_owns_file(pkg, doc["file"], packages)
        ]
        print(f"  {pkg['name']}: {len(idx_entries)} index-entries, {len(term_entries)} terms")
        entry_docs.extend(idx_entries)
        entry_docs.extend(term_entries)
        entry_docs.extend(doc for doc in collect_claim_entries(pkg)
                          if package_owns_file(pkg, doc["file"], packages))

    chunk_docs = collect_catalog_article_chunks(packages)
    if corpus_fingerprint(catalog_path) != before:
        raise RuntimeError("Knowledge changed during collection; retry the index refresh")
    setup_entries_index(client, entry_docs, index_name=entries_index)
    print(f"  published articles: {len(chunk_docs)} chunks")
    setup_chunks_index(client, chunk_docs, index_name=chunks_index)
    if corpus_fingerprint(catalog_path) != before:
        raise RuntimeError("Knowledge changed during indexing; retry the index refresh")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知識連合インデクサ")
    parser.add_argument(
        "--catalog",
        default=DEFAULT_CATALOG,
        help=f"CATALOG.md のパス (default: {DEFAULT_CATALOG})",
    )
    parser.add_argument(
        "--meili-url",
        default=os.environ.get("MEILI_URL", DEFAULT_MEILI_URL),
        help=f"Meilisearch URL (default: {DEFAULT_MEILI_URL})",
    )
    parser.add_argument(
        "--index-prefix",
        default="",
        help="インデックス名のプレフィックス (例: test-)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Emit derived documents as JSON without connecting to a backend")
    args = parser.parse_args()
    catalog = resolve_catalog_path(args.catalog)
    if args.dry_run:
        for package in read_catalog_packages(catalog, strict=True):
            entry = package.entry_path
            if not (entry.is_dir() if entry == package.root else entry.is_file()):
                parser.error(f"Invalid Catalog entrance: {entry}")
        packages = parse_catalog(catalog)
        entries = [doc for pkg in packages for collect in (collect_index_entries, collect_term_entries, collect_claim_entries)
                   for doc in collect(pkg) if package_owns_file(pkg, doc["file"], packages)]
        print(json.dumps({"entries": entries, "chunks": collect_catalog_article_chunks(packages)}, ensure_ascii=False, indent=2))
    else:
        main(catalog, args.meili_url, args.index_prefix)
