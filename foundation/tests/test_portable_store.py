"""Synthetic contracts spanning current claims, publication and configured backends."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from federation_core import core
from federation_core.articles import read_article
from federation_core.locators import current_entry
from federation_core.publication import PublicationScope
import check
import indexer
import server

SAMPLE = Path(__file__).resolve().parents[2] / 'examples/minimal'


@pytest.fixture
def store(tmp_path):
    root = tmp_path / 'store'
    shutil.copytree(SAMPLE, root)
    return root, root / 'CATALOG.md', root / 'articles/measurement/window-mean.md'


@contextmanager
def backend(article, *, degraded=False):
    """Real HTTP transport, deterministic synthetic responses; no embedding model."""
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.reply(200, {'status': 'available'})
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, payload, self.headers.get('Authorization')))
            if self.path == '/indexes/entries/search':
                self.reply(200, {'hits': []})
            elif degraded and 'hybrid' in payload:
                self.reply(503, {'message': 'Synthetic embedder unavailable'})
            else:
                self.reply(200, {'hits': [{'file': str(article), 'title': 'Stale title',
                                         'section': 'Calculation', '_rankingScore': .95}]})
        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{http.server_port}'
    try:
        yield url, requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_explicit_configuration_and_sample_syntax(store, monkeypatch):
    root, catalog, article = store
    for key in ('FEDERATION_CATALOG', 'KNOWLEDGE_CATALOG'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match='Set FEDERATION_CATALOG'):
        core.get_catalog()
    monkeypatch.setenv('KNOWLEDGE_CATALOG', str(catalog))
    assert core.get_catalog() == catalog.read_text()
    packages = core.parse_catalog(catalog, strict=True)
    assert len(packages) == 3
    assert packages[1].entry_path == root / 'rules/tooling.md'
    assert packages[1].root == root / 'rules'
    assert check.check(catalog) == []
    catalog.write_text(catalog.read_text().replace('## Packages', '## パッケージ'))
    assert core.parse_catalog(catalog, strict=True) == packages


def test_offline_candidates_and_explicit_grep(store):
    _, catalog, article = store
    with patch.object(core, '_meili_request', side_effect=AssertionError('No network')):
        shallow = core.search('window mean', deep=False, catalog_path=catalog)
        assert shallow['semantic_status'] == 'not_requested'
        assert shallow['index_results'][0]['path'] == str(article)
        assert shallow['fulltext_results'] == []
        assert set(shallow) == core.PUBLIC_RESPONSE_KEYS
        assert core.search('UNIT_REQUIRED', catalog_path=catalog)['fulltext_results'] == []
        grep = core.grep('unit_required', catalog_path=catalog)
        assert len(grep['fulltext_results']) == 1
        assert 'UNIT_REQUIRED' not in json.dumps(grep['fulltext_results'])
        assert 'semantic_status' not in grep
        assert core.search('mean', catalog_path=catalog)['semantic_status'] == 'unavailable'
    for marker in ('DRAFT_SENTINEL', 'WORKLOG_SENTINEL', 'STUDY_SENTINEL'):
        assert core.grep(marker, catalog_path=catalog)['fulltext_results'] == []
        assert core.search(marker, deep=False, catalog_path=catalog)['index_results'] == []


@pytest.mark.parametrize('degraded', [False, True])
def test_http_backend_success_degradation_and_withdrawal(store, monkeypatch, degraded):
    root, catalog, article = store
    monkeypatch.setenv('MEILI_API_KEY', 'synthetic-test-key')
    with backend(article, degraded=degraded) as (url, requests):
        result = core.search('mean', catalog_path=catalog, meili_url=url)
        assert result['semantic_status'] == ('degraded' if degraded else 'available')
        assert result['fulltext_results'] == []
        row = result['article_results'][0]
        assert row['title'] == 'Computing a window mean'
        assert row['claim_source']['path'] == str(article)
        assert row['anchor'] == 'calculation'
        assert all(auth == 'Bearer synthetic-test-key' for _, _, auth in requests)
        assert any('hybrid' in payload for _, payload, _ in requests)
        if degraded:
            assert any(path.endswith('/chunks/search') and 'hybrid' not in payload for path, payload, _ in requests)
        article.rename(root / 'drafts/withdrawn.md')
        assert core.search('mean', catalog_path=catalog, meili_url=url)['article_results'] == []
    # The mock server is shut down and the socket closed: a configured outage.
    unavailable = core.search('Window', catalog_path=catalog, meili_url=url)
    assert unavailable['semantic_status'] == 'unavailable'
    assert unavailable['warnings'] and unavailable['index_results']
    assert unavailable['article_results'] == unavailable['fulltext_results'] == []


def test_indexer_documents_and_completed_backend_tasks(store):
    _, catalog, article = store
    packages = indexer.parse_catalog(catalog)
    entries = [doc for pkg in packages for doc in indexer.collect_claim_entries(pkg)]
    chunks = indexer.collect_catalog_article_chunks(packages)
    assert len(entries) == 1 and entries[0]['kind'] == 'article-claim'
    assert len(chunks) == 3
    assert {doc['file'] for doc in chunks} == {str(article)}
    assert all('claims:' not in doc['text'] for doc in chunks)
    client = Mock()
    index = client.index.return_value
    index.get_documents.return_value = SimpleNamespace(results=[SimpleNamespace(id='obsolete')], total=1)
    task = SimpleNamespace(task_uid=1)
    for name in ('delete_documents', 'update_searchable_attributes', 'update_typo_tolerance',
                 'update_displayed_attributes', 'update_filterable_attributes', 'add_documents'):
        getattr(index, name).return_value = task
    client.wait_for_task.return_value = SimpleNamespace(status='succeeded')
    with patch.object(indexer.meilisearch, 'Client', return_value=client):
        indexer.main(catalog, 'http://fixture')
    assert client.index.call_count == 2
    assert index.delete_documents.call_args.args == (['obsolete'],)
    assert index.add_documents.call_count == 2
    assert client.wait_for_task.call_count == 11
    index.update_embedders.assert_not_called()
    client.wait_for_task.return_value = SimpleNamespace(status='failed', error='synthetic failure')
    with pytest.raises(RuntimeError, match='synthetic failure'):
        indexer.setup_entries_index(client, entries)
    with pytest.raises(ValueError, match='Set MEILI_URL'):
        indexer.main(catalog, '')


def test_configured_embedding_settings(store, monkeypatch):
    _, catalog, _ = store
    client = Mock()
    client.index.return_value.get_documents.return_value = SimpleNamespace(results=[], total=0)
    client.wait_for_task.return_value = SimpleNamespace(status='succeeded')
    monkeypatch.setenv('OLLAMA_EMBED_URL', 'http://fixture/api/embed')
    monkeypatch.setenv('OLLAMA_EMBED_MODEL', 'synthetic-model')
    indexer.setup_chunks_index(client, [])
    settings = client.index.return_value.update_embedders.call_args.args[0]['default']
    assert settings['url'] == 'http://fixture/api/embed'
    assert settings['model'] == 'synthetic-model'


def test_claim_changes_invalid_metadata_and_static_notebook(store):
    _, catalog, article = store
    parsed = read_article(article)
    old = parsed.claims[0]
    cached = {'kind': 'article-claim', 'text': old.text, 'line': old.line}
    article.write_text(article.read_text().replace('claims:', '\n\nclaims:'))
    assert current_entry(article, cached)[0] == old.line + 2
    article.write_text(article.read_text().replace('equally weighted samples', 'equally weighted observations'))
    assert current_entry(article, cached) is None
    for claims in ('[]', '[42]', 'wrong', '[""]', '&x [*x]'):
        article.write_text(f'---\nclaims: {claims}\n---\n# Title\nBody\n')
        assert read_article(article).error
        assert PublicationScope(article.parent).allows_article(article)
        assert any(f.code == 'invalid-claims' for f in check.check(catalog))
    notebook = article.with_suffix('.py')
    notebook.write_text('raise RuntimeError("must never execute")\nmo.md("---\\nclaims: [Static claim]\\n---\\n# Static notebook\\nBody")\n')
    assert read_article(notebook).claims[0].text == 'Static claim'
    assert PublicationScope(article.parent).allows_article(notebook)


def test_scope_symlinks_and_query_budgets(store, tmp_path):
    root, catalog, article = store
    outside = tmp_path / 'outside.md'
    outside.write_text('OUTSIDE_SENTINEL')
    (article.parent / 'escape.md').symlink_to(outside)
    (article.parent / 'draft.md').symlink_to(root / 'drafts/pending.md')
    for marker in ('OUTSIDE_SENTINEL', 'DRAFT_SENTINEL'):
        assert core.grep(marker, catalog_path=catalog)['fulltext_results'] == []
    catalog.write_text(catalog.read_text() + '- [nested-work](worklogs/) — Excluded even as a package.\n')
    assert core.grep('WORKLOG_SENTINEL', catalog_path=catalog)['fulltext_results'] == []
    for call in (core.search, core.grep):
        with pytest.raises(ValueError, match='non-empty'):
            call(' ', catalog_path=catalog)
        huge = call('🦉' * 200, catalog_path=catalog)
        assert huge['omitted']['query'] and huge['truncated']
        assert len(json.dumps(huge, ensure_ascii=False, indent=2).encode()) <= 16384
    article.write_text('---\nclaims: ["' + 'x' * 5000 + '"]\n---\n# Huge\n')
    row = core.search('xxxx', deep=False, catalog_path=catalog)['index_results'][0]
    assert 'claim:byte_limit' in row['omitted']
    assert len(json.dumps(row, ensure_ascii=False, indent=2).encode()) <= 2048


def test_ui_startup_browse_assets_and_offline_actions(store, monkeypatch):
    root, catalog, article = store
    monkeypatch.setattr(server, 'CATALOG_PATH', catalog)
    monkeypatch.setattr(server, 'DRAFTS_ROOT', root / 'drafts')
    with patch.object(server, 'run_indexer', side_effect=AssertionError('Startup must not index')):
        with TestClient(server.app) as client:
            assert client.get('/').status_code == 200
            assert len(client.get('/api/tree').json()['packages']) == 3
            response = client.get('/article', params={'file': str(article)})
            assert response.status_code == 200 and 'Computing a window mean' in response.text
            assert 'id="calculation"' in response.text
            for asset in ('water.min.css', 'katex.min.css', 'katex.min.js', 'mermaid.min.js'):
                assert client.get('/static/browser-assets/' + asset).status_code == 200
            assert client.get('/api/drafts').json()['children']
            assert client.get('/api/search', params={'q': 'window mean', 'deep': 'false'}).json()['index_results']
            assert client.get('/api/grep', params={'q': 'UNIT_REQUIRED'}).json()['fulltext_results']
            assert client.get('/api/suggest', params={'q': 'mean'}).json() == {'results': []}
            assert client.post('/api/reindex').status_code == 503
            assert client.get('/article', params={'file': str(root.parent / 'outside.md')}).status_code == 404
            notebook = article.with_suffix('.py')
            notebook.write_text('mo.md("# Static")')
            assert client.post('/api/live-marimo', params={'file': str(notebook)}).status_code == 403


def test_ui_catalog_scope_changes_without_automatic_indexing(store, monkeypatch):
    _, catalog, article = store
    monkeypatch.setattr(server, 'CATALOG_PATH', catalog)
    monkeypatch.setattr(server, 'PACKAGE_ROOTS', server.parse_catalog_roots(catalog))
    assert server.is_safe_path(str(article))
    catalog.write_text('## Packages\n- [rules](rules/tooling.md) — Rules\n')
    assert not server.is_safe_path(str(article))


def test_article_url_uses_explicit_public_base(monkeypatch, tmp_path):
    from article_url import build_article_url
    monkeypatch.setenv('KNOWLEDGE_UI_BASE_URL', 'http://127.0.0.1:17776/')
    url = build_article_url(tmp_path / 'space name.md')
    assert url.startswith('http://127.0.0.1:17776/article?file=')
    assert '%20' in url
    article = tmp_path / 'nested' / 'article.md'
    encoded = build_article_url(article, base_url='http://127.0.0.1:17776')
    assert encoded == (
        'http://127.0.0.1:17776/article?file=' + quote(str(article.resolve()), safe='')
    )


def test_article_url_cli_uses_explicit_ui_host_and_port(tmp_path, monkeypatch):
    import article_url
    article = tmp_path / 'article with spaces.md'
    monkeypatch.delenv('KNOWLEDGE_UI_BASE_URL', raising=False)
    monkeypatch.setenv('UI_HOST', '127.0.0.1')
    monkeypatch.setenv('UI_PORT', '17776')
    completed = subprocess.run(
        [sys.executable, str(Path(article_url.__file__)), str(article)],
        text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == (
        'http://127.0.0.1:17776/article?file=' + quote(str(article.resolve()), safe='')
    )
