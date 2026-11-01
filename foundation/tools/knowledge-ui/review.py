#!/usr/bin/env python3
"""Article review records and agent-facing CLI. Notifiers are opaque executables."""
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'integrations/federation-core'))
from federation_core.comments import comment_path
from comments import locked, mutate, parse

MESSAGE = ('[knowledge-review] {path} round {round} {verdict} が届きました。'
           'review.py show で読み、write-article の「レビューを受け取る」手順に従ってください。')
VERDICTS = {'revise', 'approve', 'revert'}


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, content):
    if path.is_symlink():
        raise ValueError('Review files must not be symlinks')
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content.encode('utf-8') if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def notifier(*args, timeout=10):
    executable = os.environ.get('KNOWLEDGE_REVIEW_NOTIFIER', '')
    if not executable:
        return 2, {'status': 'unconfigured'}
    if not Path(executable).is_absolute():
        return 1, {'status': 'failed'}
    try:
        result = subprocess.run([executable, *map(str, args)], capture_output=True, text=True, timeout=timeout)
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError('Expected object')
        return result.returncode, data
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 1, {'status': 'failed'}


class Review:
    def __init__(self, path, catalog=None):
        if any(char in str(path) for char in '\r\n'):
            raise ValueError('Article path must fit on one line')
        self.sidecar = comment_path(path, catalog_path=catalog)
        self.article = Path(path).resolve()
        self.root = self.article.parent / '.reviews' / self.article.stem
        self.check_paths()

    def check_paths(self):
        for directory in (self.root.parent, self.root, self.root / 'rounds'):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise ValueError('Review directories must be real directories')
        if self.root.exists():
            for base, dirs, files in os.walk(self.root, followlinks=False):
                if any((Path(base) / name).is_symlink() for name in dirs + files):
                    raise ValueError('Review state must not contain symlinks')

    @contextmanager
    def lock(self):
        # Same directory lock as comment mutations; never nest this lock.
        with locked(self.sidecar):
            self.check_paths()
            if self.sidecar.is_symlink():
                raise ValueError('Comment files must not be symlinks')
            yield

    def comments(self):
        return parse(self.sidecar.read_text(encoding='utf-8')) if self.sidecar.exists() else []

    def rounds(self):
        directory = self.root / 'rounds'
        return sorted(int(p.name) for p in directory.iterdir() if p.name.isdecimal() and (p / 'submission.json').is_file()) if directory.exists() else []

    def round_dir(self, number):
        if type(number) is not int or number < 1 or number not in self.rounds():
            raise ValueError('Review round not found')
        return self.root / 'rounds' / str(number)

    def claim(self):
        code, destination = notifier('whoami')
        if code:
            print('Review owner was not recorded: notifier unavailable or identity unknown.', file=sys.stderr)
            return False
        with self.lock():
            self.root.mkdir(parents=True, exist_ok=True)
            write_json(self.root / 'owner.json', {**destination, 'at': now()})
        return True

    def can_submit(self):
        numbers = self.rounds()
        if not numbers:
            return True
        directory = self.round_dir(numbers[-1])
        submission = load(directory / 'submission.json')
        if submission['verdict'] == 'approve':
            return load(self.root / 'owner.json', {}).get('at') != submission.get('owner_at')
        return (directory / 'reply.json').exists()

    def submit(self, verdict, revert_to=None):
        if verdict not in VERDICTS:
            raise ValueError('Unknown verdict')
        with self.lock():
            if not self.can_submit():
                raise ValueError('Waiting for an agent reply or a new claim after approval')
            if verdict == 'revert':
                self.round_dir(revert_to)
            elif revert_to is not None:
                raise ValueError('revertTo is only valid for revert')
            numbers = self.rounds()
            number = numbers[-1] + 1 if numbers else 1
            text = self.article.read_bytes()
            text.decode('utf-8')
            owner = load(self.root / 'owner.json')
            submission = {'verdict': verdict, 'comment_ids': [c['id'] for c in self.comments() if c['status'] == 'open'],
                          'base_sha': hashlib.sha256(text).hexdigest(), 'at': now(),
                          'owner_at': (owner or {}).get('at')}
            if verdict == 'revert':
                submission['revertTo'] = revert_to
            rounds = self.root / 'rounds'
            rounds.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='.pending-', dir=rounds))
            try:
                write_json(stage / 'submission.json', submission)
                atomic(stage / 'base.md', text)
                write_json(stage / 'delivery.json', {'status': 'waiting', 'at': now()})
                os.rename(stage, rounds / str(number))
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        return number, owner

    def message(self, number):
        submission = load(self.round_dir(number) / 'submission.json')
        return MESSAGE.format(path=self.article, round=number, verdict=submission['verdict'])

    def deliver(self, number, owner):
        if owner is None:
            result = {'status': 'absent'}
        else:
            # Capture the recipient at submission, even if another agent claims later.
            with tempfile.TemporaryDirectory() as temp:
                destination = Path(temp) / 'owner.json'
                write_json(destination, owner)
                code, result = notifier('deliver', destination, self.message(number), timeout=1810)
                result = {k: v for k, v in result.items() if k in {'status', 'title'}}
                result['status'] = {0: 'delivered', 2: 'absent', 3: 'blocked', 4: 'timeout'}.get(code, 'failed')
        with self.lock():
            write_json(self.round_dir(number) / 'delivery.json', {**result, 'at': now()})

    def state(self):
        with self.lock():
            numbers = self.rounds()
            owner = load(self.root / 'owner.json')
            rounds = [{'round': n, **load(self.round_dir(n) / 'submission.json')} for n in numbers]
            latest = self.round_dir(numbers[-1]) if numbers else None
            reply = load(latest / 'reply.json', {}) if latest else {}
            delivery = load(latest / 'delivery.json', {}) if latest else {}
            result = {'rounds': rounds, 'can_submit': self.can_submit(), 'owner': None,
                      'reply': {'summary': reply.get('summary', ''), 'orders': [
                          {k: v for k, v in item.items() if k in {'comment_id', 'status', 'reason', 'needsConfirm', 'question'}}
                          for item in reply.get('orders', [])]}, 'delivery': delivery}
            if latest and delivery.get('status') in {'unconfigured', 'absent', 'failed', 'timeout', 'blocked'}:
                result['fallback'] = self.message(numbers[-1])
        if owner:
            code, status = notifier('status', self.root / 'owner.json')
            result['owner'] = status if not code else {'status': 'absent'}
        return result

    def skeleton(self, number):
        submission = load(self.round_dir(number) / 'submission.json')
        return {'round': number, 'summary': '', 'orders': [
            {'comment_id': id, 'status': 'done', 'reading': ''} for id in submission['comment_ids']]}

    def validate_reply(self, number, reply):
        submission = load(self.round_dir(number) / 'submission.json')
        if submission['verdict'] == 'approve':
            raise ValueError('Approval completes review; confirm Stock in conversation instead')
        if not isinstance(reply, dict) or type(reply.get('round')) is not int or reply['round'] != number:
            raise ValueError('Reply round must match')
        orders = reply.get('orders')
        if not isinstance(orders, list) or not all(isinstance(item, dict) for item in orders):
            raise ValueError('orders must be a list of objects')
        ids = [item.get('comment_id') for item in orders]
        if not all(isinstance(id, str) for id in ids) or len(set(ids)) != len(ids) or set(ids) != set(submission['comment_ids']):
            raise ValueError('orders must match submitted comments one to one')
        if 'summary' in reply and not isinstance(reply['summary'], str):
            raise ValueError('summary must be text')

        def validate(item, statuses):
            if item.get('status') not in statuses or not isinstance(item.get('reading'), str) or not item['reading'].strip() or len(item['reading'].splitlines()) != 1:
                raise ValueError('Each reply needs a status and one-line reading')

        for item in orders:
            validate(item, ('done', 'partial', 'skipped'))
            if item['status'] in {'partial', 'skipped'} and (not isinstance(item.get('reason'), str) or not item['reason'].strip()):
                raise ValueError('partial/skipped requires reason')
            if 'needsConfirm' in item and not isinstance(item['needsConfirm'], bool):
                raise ValueError('needsConfirm must be boolean')
            if item.get('needsConfirm') and (not isinstance(item.get('question'), str) or not item['question'].strip()):
                raise ValueError('needsConfirm requires question')
        if 'general' in reply:
            if not isinstance(reply['general'], dict):
                raise ValueError('general must be an object')
            validate(reply['general'], ('done', 'partial', 'skipped', 'noted'))
        if submission['verdict'] == 'revert' and any(item['status'] != 'skipped' or item.get('reason') != 'revert' for item in orders):
            raise ValueError('Revert replies must skip all orders with reason revert')

    def write_reply(self, number, reply):
        """Caller holds the comment directory lock through all state changes."""
        directory = self.round_dir(number)
        if number != self.rounds()[-1] or (directory / 'reply.json').exists():
            raise ValueError('Only the unanswered latest round accepts a reply')
        self.validate_reply(number, reply)
        submission = load(directory / 'submission.json')
        if submission['verdict'] == 'revert' and self.article.read_bytes() != (self.round_dir(submission['revertTo']) / 'base.md').read_bytes():
            raise ValueError('Run revert to restore the selected base before replying')
        for item in reply['orders']:
            if item['status'] == 'done' and any(c['id'] == item['comment_id'] for c in self.comments()):
                mutate(self.sidecar, comment_id=item['comment_id'], status='resolved', lock_held=True)
        code, destination = notifier('whoami')
        if not code:
            write_json(self.root / 'owner.json', {**destination, 'at': now()})
        else:
            print('Review owner was not recorded: notifier unavailable or identity unknown.', file=sys.stderr)
        # Publish reply last so the next submission sees resolved comments and owner.
        write_json(directory / 'reply.json', reply)

    def reply(self, number, reply):
        with self.lock():
            self.write_reply(number, reply)

    def revert(self, number):
        with self.lock():
            submission = load(self.round_dir(number) / 'submission.json')
            if submission['verdict'] != 'revert' or number != self.rounds()[-1] or (self.round_dir(number) / 'reply.json').exists():
                raise ValueError('Expected latest unanswered revert round')
            text = (self.round_dir(submission['revertTo']) / 'base.md').read_bytes()
            reply = self.skeleton(number)
            for item in reply['orders']:
                item.update(status='skipped', reading='指定版へ戻しました', reason='revert')
            self.validate_reply(number, reply)
            atomic(self.article, text)
            self.write_reply(number, reply)

    def show(self, number=None):
        numbers = self.rounds()
        if not numbers:
            raise ValueError('No review rounds')
        number = number if number is not None else numbers[-1]
        submission = load(self.round_dir(number) / 'submission.json')
        lines = [f'# Review round {number}', f'Verdict: {submission["verdict"]}']
        if submission['verdict'] == 'approve':
            lines.append('Stock の判定を会話で確かめる。')
        if 'revertTo' in submission:
            lines.append(f'Revert to round: {submission["revertTo"]}')
        items = {c['id']: c for c in self.comments()}
        for id in submission['comment_ids']:
            item = items.get(id)
            lines.append(f'\n## Comment {id}')
            lines.append(f'Heading: {item["heading"]}\n\nQuote:\n{item["quote"]}\n\nComment:\n{item["body"]}' if item else '(Comment removed)')
        if number > 1:
            previous = load(self.round_dir(number - 1) / 'reply.json', {})
            for item in previous.get('orders', []):
                if item.get('needsConfirm'):
                    lines.append(f'\n## Previous question ({item["comment_id"]})\n{item["question"]}')
                    lines.extend(f'Answer: {c["body"]}' for c in items.values()
                                 if c.get('reply_to') == item['comment_id'] and c['id'] in submission['comment_ids'])
        return '\n'.join(lines)

    def diff(self, number):
        before = (self.round_dir(number) / 'base.md').read_text(encoding='utf-8')
        after = self.article.read_text(encoding='utf-8')
        tokenize = lambda text: re.findall(r'[A-Za-z0-9_]+|[^A-Za-z0-9_]', text)
        a, b = tokenize(before), tokenize(after)
        result = []
        for tag, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag in {'delete', 'replace'}:
                result.append({'kind': 'delete', 'text': ''.join(a[i:j])})
            if tag in {'insert', 'replace', 'equal'}:
                result.append({'kind': 'equal' if tag == 'equal' else 'insert', 'text': ''.join(b[k:l])})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('claim', 'show', 'skeleton', 'reply', 'revert'):
        command = commands.add_parser(name)
        command.add_argument('path')
        if name == 'show':
            command.add_argument('--round', type=int)
        elif name in {'skeleton', 'reply', 'revert'}:
            command.add_argument('round', type=int)
        if name == 'reply':
            command.add_argument('reply_file', type=Path)
    args = parser.parse_args()
    try:
        review = Review(args.path)
        if args.command == 'claim':
            review.claim()
        elif args.command == 'show':
            print(review.show(args.round))
        elif args.command == 'skeleton':
            print(json.dumps(review.skeleton(args.round), ensure_ascii=False, indent=2))
        elif args.command == 'reply':
            review.reply(args.round, load(args.reply_file))
        elif args.command == 'revert':
            review.revert(args.round)
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
