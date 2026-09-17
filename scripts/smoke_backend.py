"""Optional real Meilisearch smoke, using a supplied binary and disposable data.

Does not start Ollama or test semantic relevance. No existing service is used.
"""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'foundation/tools/knowledge-ui'))
import indexer
from federation_core import search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, help='Path to a Meilisearch executable')
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    for key in list(os.environ):
        if key.startswith(('MEILI_', 'OLLAMA_', 'KNOWLEDGE_', 'FEDERATION_')):
            os.environ.pop(key)
    with tempfile.TemporaryDirectory(prefix='knowledge-backend-') as directory:
        root = Path(directory)
        store = root / 'store'
        shutil.copytree(ROOT / 'examples/minimal', store)
        catalog = store / 'CATALOG.md'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen([binary, '--no-analytics', '--env', 'development',
                                     '--http-addr', f'127.0.0.1:{port}', '--db-path', str(root / 'db'),
                                     '--dump-dir', str(root / 'dumps'), '--snapshot-dir', str(root / 'snapshots')],
                                    cwd=root, stdout=log, stderr=log)
            try:
                for _ in range(200):
                    try:
                        with urlopen(url + '/health', timeout=1):
                            break
                    except OSError:
                        if proc.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read().decode())
                        time.sleep(.1)
                else:
                    raise RuntimeError('Disposable backend startup timeout')
                indexer.main(catalog, url)
                found = search('window mean', catalog_path=catalog, meili_url=url)
                assert found['semantic_status'] == 'degraded', found
                assert found['article_results'] and found['fulltext_results'] == [], found
                article = store / 'articles/measurement/window-mean.md'
                article.rename(store / 'drafts/withdrawn.md')
                assert search('window mean', catalog_path=catalog, meili_url=url)['article_results'] == []
                indexer.main(catalog, url)
                assert not indexer.meilisearch.Client(url).index('chunks').get_documents().results
                print('Real Meilisearch: indexing, lexical fallback, live withdrawal and stale-document deletion passed')
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        result = search('Window', catalog_path=catalog, meili_url=url)
        assert result['semantic_status'] == 'unavailable' and result['index_results']
        print('Stopped backend: unavailable with live entries passed')
    print('Disposable backend and data removed; semantic model not tested')


if __name__ == '__main__':
    main()
