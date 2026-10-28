"""Comment CRUD and explicit reads use only temporary synthetic stores."""
import asyncio
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from federation_core import comments, grep, read
from federation_core.comments import comment_path
from federation_core.publication import PublicationScope
import indexer
import server
from comments import mutate


@pytest.fixture
def store(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    article = package / "guide.md"
    article.write_text("# Guide\n\n## Evidence\nSynthetic passage.\n")
    catalog = tmp_path / "CATALOG.md"
    catalog.write_text("## Packages\n- [fixture](package/) — Synthetic package\n")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    monkeypatch.setattr(server, "CATALOG_PATH", catalog)
    monkeypatch.setattr(server, "DRAFTS_ROOT", drafts)
    monkeypatch.setenv("FEDERATION_CATALOG", str(catalog))
    return catalog, article, TestClient(server.app)


def add(client, article, body="Please clarify."):
    return client.post("/api/comments", json={"file": str(article), "heading": "Evidence",
                       "anchor": "evidence", "quote": "Synthetic passage.\nSecond line.", "body": body})


def test_crud_preserves_other_comments_and_broken_text(store):
    catalog, article, client = store
    target = comment_path(article, catalog_path=catalog)
    assert target == article.parent / ".comments/guide.md"
    assert comments(article)["exists"] is False
    assert client.get("/api/comments", params={"file": str(article)}).json() == {"comments": []}
    response = add(client, article)
    assert response.status_code == 200
    first = response.json()["comments"][0]
    assert first["status"] == "open"
    assert "+00:00" in first["created"]
    assert len(first["id"]) == 32
    broken = '\nHandwritten note.\n<!-- comment:' + 'a' * 32 + ' -->\ninvalid metadata\n<!-- /comment:' + 'a' * 32 + ' -->\n'
    with target.open("a") as stream:
        stream.write(broken)
    second = add(client, article, "Other comment\n### Comment\n<!-- comment:synthetic -->\nUnicode: \u2028 \u0085").json()["comments"][1]
    assert broken in target.read_text()
    for status in ("resolved", "open"):
        response = client.patch(f'/api/comments/{first["id"]}', json={"file": str(article), "status": status})
        assert response.status_code == 200
        assert response.json()["comments"][0]["status"] == status
        assert response.json()["comments"][1] == second
        assert broken in target.read_text()
    # A new client reads the file as its only source of truth.
    listed = TestClient(server.app).get("/api/comments", params={"file": str(article)}).json()["comments"]
    assert listed == [first, second]
    response = client.delete(f'/api/comments/{first["id"]}', params={"file": str(article)})
    assert response.json()["comments"] == [second]
    assert broken in target.read_text()
    before = target.read_bytes()
    assert client.delete('/api/comments/missing', params={"file": str(article)}).status_code == 404
    assert target.read_bytes() == before
    assert comments(article)["content"] == target.read_text()


def test_drafts_and_input_validation(store):
    catalog, article, client = store
    draft = catalog.parent / "drafts/draft.md"
    draft.write_text("# Draft\n")
    assert add(client, draft).status_code == 200
    assert comments(draft)["exists"] is True
    with pytest.raises(ValueError):
        read(draft)
    assert add(client, article, " ").status_code == 400
    assert client.post('/api/comments', json={"file": str(article), "quote": "", "body": "x"}).status_code == 422
    assert client.patch('/api/comments/unknown', json={"file": str(article), "status": "unknown"}).status_code == 422


@pytest.mark.parametrize("kind", ["relative", "outside", "article_symlink", "directory_symlink", "sidecar_symlink", "sidecar_directory_symlink", "python", "target_python", "missing", "directory", "sidecar_article"])
def test_unsafe_paths_rejected_by_all_apis_and_core(store, kind):
    catalog, article, client = store
    outside = catalog.parent / "outside"
    outside.mkdir()
    secret = outside / "guide.md"
    secret.write_text("Synthetic outside text")
    if kind == "relative":
        article = Path("package/guide.md")
    elif kind == "outside":
        article = secret
    elif kind == "article_symlink":
        article.unlink()
        article.symlink_to(secret)
    elif kind == "directory_symlink":
        link = article.parent / "link"
        link.symlink_to(outside, target_is_directory=True)
        article = link / "guide.md"
    elif kind == "sidecar_symlink":
        article.parent.joinpath('.comments').mkdir()
        article.parent.joinpath('.comments/guide.md').symlink_to(secret)
    elif kind == "sidecar_directory_symlink":
        article.parent.joinpath('.comments').symlink_to(outside, target_is_directory=True)
    elif kind == "python":
        article = article.with_suffix(".py")
        article.write_text("# Synthetic executable\n")
    elif kind == "target_python":
        executable = article.with_suffix('.py')
        executable.write_text('# Synthetic executable\n')
        article.unlink()
        article.symlink_to(executable)
    elif kind == "missing":
        article.unlink()
    elif kind == "directory":
        article.unlink()
        article.mkdir()
    elif kind == "sidecar_article":
        article = article.parent / '.comments/other.md'
        article.parent.mkdir()
        article.write_text("# Comments\n")
    with pytest.raises(ValueError):
        comments(article)
    for response in [client.get('/api/comments', params={"file": str(article)}), add(client, article),
                     client.patch('/api/comments/unknown', json={"file": str(article), "status": "resolved"}),
                     client.delete('/api/comments/unknown', params={"file": str(article)})]:
        assert 400 <= response.status_code < 500
    assert secret.read_text() == "Synthetic outside text"


def test_comments_excluded_from_tree_search_index_and_publication(store):
    catalog, article, client = store
    add(client, article, "unique_sidecar_needle")
    target = comment_path(article)
    assert '.comments' not in client.get('/api/tree').text
    assert '.comments' not in client.get('/api/drafts').text
    assert not PublicationScope(article.parent).allows(target)
    with pytest.raises(ValueError):
        read(target)
    result = grep("unique_sidecar_needle", catalog_path=catalog)
    assert not result['fulltext_results']
    packages = indexer.parse_catalog(catalog)
    chunks = indexer.collect_catalog_article_chunks(packages)
    entries = indexer.collect_claim_entries(packages[0]) + indexer.collect_term_entries(packages[0]) + indexer.collect_index_entries(packages[0])
    assert 'unique_sidecar_needle' not in str(chunks + entries)
    before = indexer.corpus_fingerprint(catalog)
    add(client, article, "Another comment")
    assert indexer.corpus_fingerprint(catalog) == before


def test_concurrent_process_writes_are_serialized(store):
    _, article, _ = store
    target = comment_path(article)
    # Real separate processes exercise the lock across workers and atomic replaces.
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(mutate, target, new={"heading": "Guide", "anchor": "guide", "quote": "Text", "body": f"Comment {i}"}) for i in range(12)]
        for future in futures:
            future.result()
    from comments import parse
    items = parse(target.read_text())
    assert len({item['id'] for item in items}) == 12
    assert {item['body'] for item in items} == {f'Comment {i}' for i in range(12)}
    assert list(target.parent.iterdir()) == [target]


def test_real_mcp_comments_tool(store):
    catalog, article, client = store
    adapter = Path(__file__).resolve().parents[1] / 'integrations/federation-mcp/server.py'

    async def exercise():
        async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(adapter)], env={"FEDERATION_CATALOG": str(catalog)})) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert 'federation_comments' in {tool.name for tool in (await session.list_tools()).tools}
                missing = await session.call_tool('federation_comments', {"path": str(article)})
                assert not missing.isError
                assert missing.structuredContent['exists'] is False
                add(client, article)
                result = await session.call_tool('federation_comments', {"path": str(article)})
                assert not result.isError
                assert result.structuredContent == comments(article)
                for path in ('relative.md', str(article.parent.parent / 'outside.md')):
                    rejected = await session.call_tool('federation_comments', {"path": path})
                    assert rejected.isError
    asyncio.run(exercise())
