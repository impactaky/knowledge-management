"""Standalone synthetic smoke: CLI/checker/indexer, actual UI HTTP and MCP stdio.

No production backend is consulted. Run using the documented uv project.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'foundation/tools/knowledge-ui'
CORE = ROOT / 'foundation/integrations/federation-core'


def clean_environment(catalog):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('FEDERATION_', 'KNOWLEDGE_', 'MEILI_', 'OLLAMA_'))}
    env.update(FEDERATION_CATALOG=str(catalog), PYTHONPATH=str(CORE),
               UV_CACHE_DIR=str(ROOT / '.cache/uv'))
    return env


def assert_sample_definition(result):
    rows = [row for row in result['index_results']
            if row.get('kind') == 'context' and row.get('section') == 'Sample']
    assert len(rows) == 1
    assert rows[0]['definition'] == (
        '**Sample**:\nOne fictional observation at a stated time and unit.'
    )


async def mcp_smoke(env):
    params = StdioServerParameters(command=sys.executable,
                                  args=[str(ROOT / 'foundation/integrations/federation-mcp/server.py')], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {'get_catalog', 'federation_search', 'federation_grep'}
            catalog = await session.call_tool('get_catalog', {})
            assert not catalog.isError and 'sample-articles' in catalog.content[0].text
            search = await session.call_tool('federation_search', {'query': 'window mean', 'deep': False})
            assert not search.isError
            result = json.loads(search.content[0].text)
            assert result['semantic_status'] == 'not_requested' and result['index_results']
            for deep in (False, True):
                glossary = await session.call_tool('federation_search', {'query': 'Sample', 'deep': deep})
                assert not glossary.isError
                assert_sample_definition(json.loads(glossary.content[0].text))
            grep = await session.call_tool('federation_grep', {'query': 'UNIT_REQUIRED'})
            assert not grep.isError and json.loads(grep.content[0].text)['fulltext_results']
    print('MCP: initialize, list_tools, get_catalog, federation_search, federation_grep passed')


def main():
    with tempfile.TemporaryDirectory(prefix='knowledge-smoke-') as directory:
        store = Path(directory) / 'store'
        shutil.copytree(ROOT / 'examples/minimal', store)
        env = clean_environment(store / 'CATALOG.md')
        def run(args):
            return subprocess.run([sys.executable, *args], env=env, cwd=ROOT,
                                  check=True, capture_output=True, text=True, timeout=30).stdout
        assert 'sample-notes' in run(['-m', 'federation_core', 'catalog'])
        assert json.loads(run(['-m', 'federation_core', 'search', 'window mean', '--shallow']))['index_results']
        assert_sample_definition(json.loads(run(['-m', 'federation_core', 'search', 'Sample', '--shallow'])))
        assert json.loads(run(['-m', 'federation_core', 'grep', 'UNIT_REQUIRED']))['fulltext_results']
        print('Core CLI: Catalog, shallow candidates and explicit grep passed')
        checked = run([str(UI / 'check.py')]).strip()
        assert checked == 'Knowledge check: 0 issue(s)'
        print(checked)
        docs = json.loads(run([str(UI / 'indexer.py'), '--dry-run']))
        assert len(docs['entries']) == 3 and len(docs['chunks']) == 3
        assert not any(marker in json.dumps(docs) for marker in ('DRAFT_SENTINEL','WORKLOG_SENTINEL','STUDY_SENTINEL'))
        print('Indexer dry-run: 3 entries, 3 chunks; excluded sentinels absent')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', str(port)],
                                    env=env, cwd=UI, stdout=log, stderr=log)
            try:
                base = f'http://127.0.0.1:{port}'
                for _ in range(100):
                    try:
                        with urlopen(base, timeout=1) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        if proc.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read().decode())
                        time.sleep(.1)
                else:
                    raise RuntimeError('UI startup timeout')
                article = store / 'articles/measurement/window-mean.md'
                with urlopen(base + '/article?' + urlencode({'file': article}), timeout=5) as response:
                    assert response.status == 200 and 'Computing a window mean' in response.read().decode()
                for asset in ('water.min.css', 'katex.min.css', 'katex.min.js', 'mermaid.min.js'):
                    with urlopen(base + '/static/browser-assets/' + asset, timeout=5) as response:
                        assert response.status == 200
                with urlopen(base + '/api/search?q=window%20mean&deep=false', timeout=5) as response:
                    assert json.load(response)['index_results']
                with urlopen(base + '/api/search?q=Sample&deep=false', timeout=5) as response:
                    assert_sample_definition(json.load(response))
                print('UI HTTP: startup, home, adopted article, 4 static assets and shallow search passed')
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        asyncio.run(mcp_smoke(env))
    print('Standalone smoke passed; temporary store and processes removed')


if __name__ == '__main__':
    main()
