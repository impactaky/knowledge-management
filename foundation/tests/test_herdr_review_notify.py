"""The optional notifier talks only to a synthetic Herdr executable."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

NOTIFIER = Path(__file__).resolve().parents[1] / 'integrations/herdr-review-notify/notifier.py'


@pytest.fixture
def transport(tmp_path):
    stub = tmp_path / 'herdr'
    stub.write_text(f'''#!{sys.executable}
import json,os,sys
from pathlib import Path
root=Path(os.environ['STUB_ROOT'])
args=sys.argv[2:]
with (root/'calls').open('a') as stream: stream.write(json.dumps(args)+'\\n')
agent={{'agent':'any-agent','pane_id':'synthetic:pane','agent_session':{{'kind':os.environ.get('SESSION_KIND','id'),'value':os.environ.get('SESSION_ID','conversation')}},'agent_status':os.environ.get('AGENT_STATUS','idle'),'terminal_title_stripped':'Synthetic conversation'}}
if (root/'waited').exists(): agent['agent_status']='idle'
if args[0]=='get': print(json.dumps({{'id':1,'result':{{'type':'agent_info','agent':agent}}}}))
if args[0]=='list': print(json.dumps({{'agents':[] if os.environ.get('ABSENT') else [agent]}}))
if args[0]=='wait':
 if os.environ.get('WAIT_TIMEOUT'): sys.exit(1)
 (root/'waited').touch()
if args[0]=='prompt': (root/'message').write_text(args[2])
''')
    stub.chmod(0o755)
    env = {**os.environ, 'HERDR_ENV': '1', 'HERDR_BIN_PATH': str(stub), 'HERDR_PANE_ID': 'synthetic:pane',
           'HERDR_SOCKET_PATH': str(tmp_path / 'synthetic.sock'), 'STUB_ROOT': str(tmp_path)}
    owner = tmp_path / 'owner.json'
    def run(*args, **extra):
        return subprocess.run([sys.executable, str(NOTIFIER), *map(str, args)], env={**env, **extra}, capture_output=True, text=True)
    response = run('whoami')
    assert response.returncode == 0
    owner.write_text(response.stdout)
    return run, owner, tmp_path


def test_identity_and_status(transport):
    run, owner, _ = transport
    assert json.loads(owner.read_text())['agent'] == 'any-agent'
    assert run('whoami', SESSION_KIND='unknown').returncode == 2
    assert run('whoami', HERDR_PANE_ID='other:pane').returncode == 2
    assert run('whoami', HERDR_ENV='0').returncode == 2
    assert run('whoami', HERDR_SOCKET_PATH='relative').returncode == 2
    assert run('whoami', HERDR_BIN_PATH=str(owner.parent / 'missing-herdr')).returncode == 2
    assert json.loads(run('status', owner).stdout)['status'] == 'idle'


@pytest.mark.parametrize('extra,code', [({}, 0), ({'AGENT_STATUS': 'done'}, 0),
    ({'SESSION_ID': 'reused-pane-conversation'}, 2), ({'ABSENT': '1'}, 2),
    ({'AGENT_STATUS': 'blocked'}, 3), ({'AGENT_STATUS': 'working'}, 0),
    ({'AGENT_STATUS': 'working', 'WAIT_TIMEOUT': '1'}, 4)])
def test_delivery(transport, extra, code):
    run, owner, root = transport
    response = run('deliver', owner, 'Synthetic review notification', **extra)
    assert response.returncode == code, response.stderr
    assert (root / 'message').exists() == (code == 0)
    calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
    if extra.get('AGENT_STATUS') == 'working':
        assert any(call[:2] == ['wait', 'synthetic:pane'] and call[-1] == '1800000' for call in calls)
    if code == 0:
        assert (root / 'message').read_text() == 'Synthetic review notification'
    if 'SESSION_ID' in extra:
        assert json.loads(response.stdout)['status'] == 'absent'


def test_modified_owner_cannot_select_reused_pane(transport):
    run, owner, root = transport
    payload = json.loads(owner.read_text())
    payload['session_id'] = 'different-conversation'
    payload['pane_hint'] = 'synthetic:pane'
    owner.write_text(json.dumps(payload))
    assert run('deliver', owner, 'Synthetic').returncode == 2
    assert not (root / 'message').exists()
