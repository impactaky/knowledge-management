"""Synthetic review lifecycle, transport boundaries and authentication."""
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from federation_core import grep, read
from federation_core.publication import PublicationScope
from comments import mutate
from review import Review
import server
import indexer
from auth import credentials
from launch import LauncherConfig, ConfigurationError


@pytest.fixture
def store(tmp_path, monkeypatch):
    package = tmp_path / 'package'
    package.mkdir()
    article = package / 'guide.md'
    article.write_text('# Guide\n\n古い text.\n')
    catalog = tmp_path / 'CATALOG.md'
    catalog.write_text('## Packages\n- [fixture](package/) — Synthetic package\n')
    (tmp_path / 'drafts').mkdir()
    monkeypatch.setenv('FEDERATION_CATALOG', str(catalog))
    monkeypatch.delenv('KNOWLEDGE_REVIEW_NOTIFIER', raising=False)
    monkeypatch.setattr(server, 'CATALOG_PATH', catalog)
    monkeypatch.setattr(server, 'DRAFTS_ROOT', tmp_path / 'drafts')
    return Review(article), TestClient(server.app), catalog


def comment(review, body='Clarify', **extra):
    return mutate(review.sidecar, new={'heading': 'Guide', 'anchor': 'guide', 'quote': 'text', 'body': body, **extra})[-1]


def fake_notifier(tmp_path, monkeypatch, code=0):
    executable = tmp_path / 'notify'
    log = tmp_path / 'notify.log'
    executable.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
with Path({str(log)!r}).open('a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1]=='whoami': print(json.dumps({{"session_id":"synthetic-session","title":"Synthetic owner"}}))
else: print(json.dumps({{"status":"idle","title":"Synthetic owner"}}))
sys.exit({code})
''')
    executable.chmod(0o755)
    monkeypatch.setenv('KNOWLEDGE_REVIEW_NOTIFIER', str(executable))
    return log


def test_lifecycle_and_publication(store, tmp_path, monkeypatch):
    review, client, catalog = store
    log = fake_notifier(tmp_path, monkeypatch)
    result = subprocess.run([sys.executable, str(Path(server.__file__).with_name('article_url.py')), str(review.article)], capture_output=True, text=True)
    assert result.returncode == 0 and '/?file=' in result.stdout
    assert json.loads((review.root / 'owner.json').read_text())['session_id'] == 'synthetic-session'
    original = comment(review, 'private_comment_needle')
    response = client.post('/api/reviews/submit', json={'file': str(review.article), 'verdict': 'revise'})
    assert response.status_code == 200
    directory = review.round_dir(1)
    assert (directory / 'base.md').read_text() == review.article.read_text()
    submission = json.loads((directory / 'submission.json').read_text())
    assert submission['comment_ids'] == [original['id']]
    assert submission['base_sha'] == hashlib.sha256(review.article.read_bytes()).hexdigest()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    delivered = next(call for call in calls if call[0] == 'deliver')
    assert delivered[2] == review.message(1)
    assert 'private_comment_needle' not in delivered[2] and '\n' not in delivered[2]
    state = client.get('/api/reviews', params={'file': str(review.article)}).json()
    assert state['delivery']['status'] == 'delivered' and not state['can_submit']
    assert client.post('/api/reviews/submit', json={'file': str(review.article), 'verdict': 'revise'}).status_code == 409
    assert 'private_comment_needle' in review.show()
    reply = review.skeleton(1)
    reply['summary'] = 'Please check the wording'
    reply['orders'][0].update(status='partial', reading='Clarified the example', reason='One choice remains', needsConfirm=True, question='Use this term?')
    review.reply(1, reply)
    state = review.state()
    assert state['can_submit'] and state['reply']['summary'] == reply['summary']
    assert 'reading' not in state['reply']['orders'][0]
    answer = client.post('/api/comments', json={'file': str(review.article), 'quote': 'text', 'body': 'ok', 'reply_to': original['id']}).json()['comments'][-1]
    assert answer['reply_to'] == original['id']
    review.article.write_text('# Guide\n\n新しい words.\n')
    diff = client.get('/api/reviews/diff', params={'file': str(review.article), 'round': 1}).json()['changes']
    assert {'insert', 'delete'} <= {c['kind'] for c in diff}
    n, _ = review.submit('revise')
    assert n == 2 and 'Use this term?' in review.show() and 'Answer: ok' in review.show()
    reply = review.skeleton(2)
    for item in reply['orders']:
        item['reading'] = 'Applied'
    review.reply(2, reply)
    assert all(c['status'] == 'resolved' for c in review.comments())
    # Hidden review state stays out of every publication surface without new exclusions.
    assert '.reviews' not in client.get('/api/tree').text
    assert '.reviews' not in client.get('/api/drafts').text
    assert not PublicationScope(review.article.parent).allows(directory / 'base.md')
    with pytest.raises(ValueError):
        read(directory / 'base.md')
    assert not grep('private_comment_needle', catalog_path=catalog)['fulltext_results']
    packages = indexer.parse_catalog(catalog)
    assert 'private_comment_needle' not in str(indexer.collect_catalog_article_chunks(packages))


@pytest.mark.parametrize('defect', ['missing', 'extra', 'duplicate', 'reading', 'reason', 'question', 'round'])
def test_invalid_reply_has_no_effect(store, defect):
    review, _, _ = store
    comment(review)
    review.submit('revise')
    reply = review.skeleton(1)
    reply['orders'][0]['reading'] = 'Read'
    if defect == 'missing': reply['orders'] = []
    if defect == 'extra': reply['orders'].append({**reply['orders'][0], 'comment_id': 'extra'})
    if defect == 'duplicate': reply['orders'] *= 2
    if defect == 'reading': reply['orders'][0]['reading'] = ''
    if defect == 'reason': reply['orders'][0]['status'] = 'partial'
    if defect == 'question': reply['orders'][0]['needsConfirm'] = True
    if defect == 'round': reply['round'] = 2
    before = review.sidecar.read_bytes()
    with pytest.raises(ValueError): review.reply(1, reply)
    assert not (review.round_dir(1) / 'reply.json').exists()
    assert before == review.sidecar.read_bytes()


def test_approve_reclaim_revert_and_no_notifier(store, tmp_path, monkeypatch, capsys):
    review, client, _ = store
    assert not review.claim()
    assert not (review.root / 'owner.json').exists()
    assert 'not recorded' in capsys.readouterr().err
    n, owner = review.submit('approve')
    review.deliver(n, owner)
    assert review.state()['fallback'] == review.message(1)
    assert not review.can_submit()
    with pytest.raises(ValueError): review.reply(1, review.skeleton(1))
    fake_notifier(tmp_path, monkeypatch)
    review.claim()
    assert review.can_submit()
    before = review.article.read_bytes()
    review.article.write_text('Changed')
    comment(review)
    review.submit('revert', 1)
    assert 'Revert to round: 1' in review.show()
    review.revert(2)
    assert review.article.read_bytes() == before
    assert json.loads((review.round_dir(2) / 'reply.json').read_text())['orders'][0]['reason'] == 'revert'


@pytest.mark.parametrize('code,status', [(1, 'failed'), (2, 'absent'), (3, 'blocked'), (4, 'timeout')])
def test_delivery_failure_fallback(store, tmp_path, monkeypatch, code, status):
    review, _, _ = store
    fake_notifier(tmp_path, monkeypatch)
    review.claim()
    number, owner = review.submit('revise')
    fake_notifier(tmp_path, monkeypatch, code)
    review.deliver(number, owner)
    assert review.state()['delivery']['status'] == status
    assert review.state()['fallback'] == review.message(number)


@pytest.mark.parametrize('component', ['.reviews', '.reviews/guide', '.reviews/guide/rounds', '.reviews/guide/owner.json', '.reviews/guide/rounds/1/base.md'])
def test_review_symlinks_rejected(store, tmp_path, component):
    review, client, _ = store
    review.submit('revise')
    path = review.article.parent / component
    outside = tmp_path / 'outside'
    if path.is_dir():
        import shutil
        shutil.rmtree(path)
        outside.mkdir()
        path.symlink_to(outside, target_is_directory=True)
    else:
        path.unlink(missing_ok=True)
        outside.write_text('Synthetic outside')
        path.symlink_to(outside)
    with pytest.raises(ValueError): Review(review.article)
    assert client.get('/api/reviews', params={'file': str(review.article)}).status_code == 400


def submit_process(arguments):
    article, catalog = arguments
    try:
        Review(article, catalog).submit('revise')
        return True
    except ValueError:
        return False


def test_concurrent_submit(store):
    review, _, _ = store
    with ProcessPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(submit_process, [(str(review.article), str(store[2]))] * 8)) == 1
    assert review.rounds() == [1]


def test_auth_startup_and_all_routes(store, tmp_path, monkeypatch):
    _, client, catalog = store
    for value in (None, str(tmp_path / 'missing')):
        env = {'FEDERATION_CATALOG': str(catalog), 'UI_HOST': '0.0.0.0'}
        if value: env['KNOWLEDGE_UI_BASIC_AUTH_FILE'] = value
        with pytest.raises(ConfigurationError, match='BASIC_AUTH_FILE'): LauncherConfig.from_env(env)
    file = tmp_path / 'auth'
    file.write_text('bad-format')
    with pytest.raises(ValueError): credentials({'UI_HOST': '0.0.0.0', 'KNOWLEDGE_UI_BASIC_AUTH_FILE': str(file)})
    salt = bytes(range(16))
    digest = hashlib.pbkdf2_hmac('sha256', b'synthetic-password', salt, 100000)
    file.write_text(f'reviewer:pbkdf2-sha256$100000${salt.hex()}${digest.hex()}\n')
    env = {'UI_HOST': '0.0.0.0', 'KNOWLEDGE_UI_BASIC_AUTH_FILE': str(file)}
    assert LauncherConfig.from_env({**env, 'FEDERATION_CATALOG': str(catalog)}).ui_host == '0.0.0.0'
    monkeypatch.setattr(server, 'UI_CREDENTIALS', credentials(env))
    for route in ('/', '/static/comments.js', '/api/tree', '/api/reviews'):
        assert client.get(route).status_code == 401
        assert client.get(route, auth=('reviewer', 'wrong')).status_code == 401
        assert client.get(route, auth=('reviewer', 'synthetic-password')).status_code != 401
    assert credentials({'UI_HOST': '::1'}) is None


def test_snapshot_sha_and_revert_preserve_bytes(store):
    review, _, _ = store
    original = '# Guide\r\n\r\n合成 text.\r\n'.encode()
    review.article.write_bytes(original)
    review.submit('revise')
    directory = review.round_dir(1)
    assert (directory / 'base.md').read_bytes() == original
    assert json.loads((directory / 'submission.json').read_text())['base_sha'] == hashlib.sha256(original).hexdigest()
    review.reply(1, review.skeleton(1))
    review.article.write_text('Changed')
    review.submit('revert', 1)
    with pytest.raises(ValueError, match='Run revert'):
        review.reply(2, review.skeleton(2))
    review.revert(2)
    assert review.article.read_bytes() == original


def test_empty_comment_and_newline_path(store):
    review, client, _ = store
    response = client.post('/api/comments', json={'file': str(review.article), 'quote': 'text', 'body': ''})
    assert response.status_code == 200
    review.submit('revise')
    assert 'Comment:' in review.show()
    path = review.article.with_name('new\nline.md')
    path.write_text('Synthetic')
    with pytest.raises(ValueError, match='one line'):
        Review(path)


def test_url_outside_notifier_environment_is_harmless(store, monkeypatch):
    review, _, _ = store
    adapter = Path(__file__).resolve().parents[1] / 'integrations/herdr-review-notify/notifier.py'
    monkeypatch.setenv('KNOWLEDGE_REVIEW_NOTIFIER', str(adapter))
    monkeypatch.setenv('HERDR_ENV', '0')
    result = subprocess.run([sys.executable, str(Path(server.__file__).with_name('article_url.py')), str(review.article)], capture_output=True, text=True)
    assert result.returncode == 0 and '/?file=' in result.stdout
    assert len(result.stderr.splitlines()) == 1
    assert not (review.root / 'owner.json').exists()


def test_optional_general_reply(store):
    review, _, _ = store
    review.submit('revise')
    reply = review.skeleton(1)
    reply['general'] = {'reading': 'Only part of the general request was clear', 'status': 'partial'}
    review.reply(1, reply)
    assert json.loads((review.round_dir(1) / 'reply.json').read_text())['general'] == reply['general']
