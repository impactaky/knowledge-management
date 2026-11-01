#!/usr/bin/env python3
"""Optional same-machine Herdr transport for knowledge review notifications."""
import json
import os
from pathlib import Path
import subprocess
import sys

TIMEOUT_MS = 1800000


def unwrap(data):
    while isinstance(data, dict) and 'result' in data:
        data = data['result']
    return data


def call(args, socket, timeout=10):
    executable = os.environ.get('HERDR_BIN_PATH') or 'herdr'
    return subprocess.run([executable, 'agent', *args], env={**os.environ, 'HERDR_SOCKET_PATH': socket},
                          capture_output=True, text=True, timeout=timeout)


def data(result):
    return unwrap(json.loads(result.stdout))


def identity(agent):
    session = agent.get('agent_session') or {}
    return session.get('value') if session.get('kind') == 'id' else None


def find(owner):
    response = call(['list'], owner['socket'])
    if response.returncode:
        return None
    payload = data(response)
    agents = payload.get('agents', []) if isinstance(payload, dict) else payload
    matches = [a for a in agents if identity(a) == owner['session_id'] and a.get('agent') == owner['agent']]
    return matches[0] if len(matches) == 1 else None


def state(agent):
    if not agent:
        return {'status': 'absent'}
    status = agent.get('agent_status', 'unknown')
    return {'status': status if status in {'idle', 'working', 'blocked', 'done'} else 'absent',
            'title': agent.get('terminal_title_stripped', '')}


def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False))
    return code


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        if command == 'whoami':
            pane, socket = os.environ.get('HERDR_PANE_ID'), os.environ.get('HERDR_SOCKET_PATH', '')
            if os.environ.get('HERDR_ENV') != '1' or not pane or not Path(socket).is_absolute():
                return emit({'status': 'absent'}, 2)
            response = call(['get', pane], socket)
            agent = data(response).get('agent', {}) if not response.returncode else {}
            session = identity(agent)
            if not session or agent.get('pane_id') != pane:
                return emit({'status': 'absent'}, 2)
            return emit({'kind': 'herdr', 'agent': agent['agent'], 'session_id': session,
                         'pane_hint': pane, 'socket': socket, 'title': agent.get('terminal_title_stripped', '')})
        if command not in {'status', 'deliver'}:
            return emit({'status': 'failed'}, 1)
        owner = json.loads(Path(sys.argv[2]).read_text())
        if owner.get('kind') != 'herdr' or not Path(owner['socket']).is_absolute() or not owner.get('session_id'):
            return emit({'status': 'absent'}, 2)
        agent = find(owner)
        status = state(agent)
        if command == 'status':
            return emit(status)
        if status['status'] == 'absent':
            return emit(status, 2)
        if status['status'] == 'blocked':
            return emit(status, 3)
        if status['status'] == 'working':
            response = call(['wait', agent['pane_id'], '--until', 'idle', '--timeout', str(TIMEOUT_MS)], owner['socket'], timeout=1810)
            if response.returncode:
                # A blocked agent may interrupt wait; distinguish it from timeout.
                status = state(find(owner))
                return emit(status if status['status'] in {'absent', 'blocked'} else {'status': 'timeout'},
                            {'absent': 2, 'blocked': 3}.get(status['status'], 4))
        # Recheck conversation identity after waiting and immediately before prompt.
        agent = find(owner)
        status = state(agent)
        if status['status'] not in {'idle', 'done'}:
            return emit(status, {'absent': 2, 'blocked': 3}.get(status['status'], 4))
        response = call(['prompt', agent['pane_id'], sys.argv[3]], owner['socket'])
        if response.returncode:
            if 'agent_blocked' in response.stdout + response.stderr:
                return emit({'status': 'blocked'}, 3)
            return emit({'status': 'failed'}, 1)
        return emit({'status': 'delivered', 'title': status.get('title', '')})
    except subprocess.TimeoutExpired:
        return emit({'status': 'timeout'}, 2 if command == 'whoami' else 4)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return emit({'status': 'failed'}, 2 if command == 'whoami' else 1)


if __name__ == '__main__':
    raise SystemExit(main())
