"""Migrate one repository's theme INDEX claims into its articles, without execution.

Run with --repo and --audit-dir. Dry-run is the default; --apply writes only
after the complete plan passes validation. --verify checks an applied audit.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap
from urllib.parse import unquote, urlsplit
import uuid

from .articles import (frontmatter_span, markdown_nodes, node_span, read_article,
                       headings, slugify, prose_lines, anchors)
from .publication import EXCLUDED_DIRS, LINK_RE, INLINE_CODE_RE, index_lines, local_references, PublicationScope


class MigrationError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _insert_markdown(text: str, claims: list[str]) -> str:
    # JSON strings are YAML strings. One physical line per claim preserves all
    # punctuation, backslashes and source suffixes without folding prose.
    lines = 'claims:\n' + ''.join('  - ' + json.dumps(c, ensure_ascii=False) + '\n' for c in claims)
    span = frontmatter_span(text)
    if span:
        return text[:span[1]] + lines + text[span[1]:]
    return '---\n' + lines + '---\n' + text


def insert_claims(path: Path, source: str, claims: list[str]) -> str:
    before = read_article(path, source=source)
    if before.error:
        raise MigrationError(f'{path}: invalid article metadata: {before.error}')
    if 'claims' in before.metadata:
        if [c.text for c in before.claims] != claims:
            raise MigrationError(f'{path}: existing claims conflict with legacy INDEX')
        return source
    if path.suffix == '.md':
        result = _insert_markdown(source, claims)
    else:
        nodes = markdown_nodes(source)
        if not nodes:
            raise MigrationError(f'{path}: no static mo.md literal')
        node = nodes[0]
        start, end = node_span(source, node)
        raw = source.encode('utf-8')[start:end].decode('utf-8')
        value = _insert_markdown(textwrap.dedent(node.value), claims)
        # Preserve the literal and its formatting when a direct insertion is
        # possible. Other literal spellings use repr, still changing only the
        # first Constant. Validate the decoded result before accepting either.
        match = re.match(r"(?is)([ru]*)(\"\"\"|''')", raw)
        replacement = repr(value)
        if match:
            prefix, quote = match.groups()
            content = raw[match.end():-len(quote)]
            # Dedent only for locating the YAML end, preserving existing text.
            close = re.search(r'(?m)^([ \t]*)---[ \t]*$', content)
            if close:
                second = re.search(r'(?m)^([ \t]*)---[ \t]*$', content[close.end():])
                if second:
                    at = close.end() + second.start()
                    indent = second[1]
                    addition = 'claims:\n' + ''.join('  - ' + json.dumps(c, ensure_ascii=False) + '\n' for c in claims)
                    addition = ''.join(indent + line for line in addition.splitlines(keepends=True))
                    if 'r' not in prefix.lower():
                        addition = addition.replace('\\', '\\\\')
                    trial = raw[:match.end()+at] + addition + raw[match.end()+at:]
                    try:
                        # For indented literals, raw value differs only in YAML
                        # indentation; comparison below checks its parsed body.
                        ast.literal_eval(trial)
                        replacement = trial
                    except (SyntaxError, ValueError):
                        pass
        result = (source.encode('utf-8')[:start] + replacement.encode('utf-8') + source.encode('utf-8')[end:]).decode('utf-8')
        old_tree, new_tree = ast.parse(source), ast.parse(result)
        old_first, new_first = markdown_nodes(source)[0], markdown_nodes(result)[0]
        # Only erase the first Markdown value for the executable-AST comparison.
        for tree, first in [(old_tree, old_first), (new_tree, new_first)]:
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and (n.lineno, n.col_offset) == (first.lineno, first.col_offset):
                    n.value = '<article frontmatter>'
                    break
        if ast.dump(old_tree) != ast.dump(new_tree):
            raise MigrationError(f'{path}: executable AST changed')
    after = read_article(path, source=result)
    metadata = {k: v for k, v in after.metadata.items() if k != 'claims'}
    if after.error or before.body != after.body or before.metadata != metadata or [c.text for c in after.claims] != claims:
        raise MigrationError(f'{path}: body, metadata or claim preservation failed')
    return result


@dataclass
class Change:
    path: Path
    before: bytes
    after: bytes | None


@dataclass
class Plan:
    repo: Path
    changes: list[Change]
    articles: list[dict]
    protected: dict[str, str]

    def summary(self) -> dict:
        return {'articles': len(self.articles), 'claims': sum(len(a['claims']) for a in self.articles),
                'changed_files': len(self.changes)}


def _repo_files(repo: Path):
    # Never descend symlinks or private generated/work directories. Learning
    # maps are inspected for navigation, but not interpreted as articles.
    for base, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in EXCLUDED_DIRS - {'books', 'studies'} and not (Path(base)/d).is_symlink())
        for name in sorted(files):
            path = Path(base)/name
            if not path.is_symlink() and path.suffix in {'.md', '.py'}:
                yield path


def build_plan(repo: Path) -> Plan:
    repo = repo.resolve(strict=True)
    layer = repo / 'articles'
    if not layer.is_dir() or layer.is_symlink():
        raise MigrationError(f'{repo}: expected a real articles directory')
    package_indexes = {repo/'INDEX.md', layer/'INDEX.md'}
    catalog = repo/'CATALOG.md'
    if catalog.exists():
        package_indexes.update(p for p, _ in local_references(catalog, catalog.read_text()) if p.is_relative_to(repo))
    for path in layer.glob('*/*'):
        if path.suffix in {'.md', '.py'} and (path.is_symlink() or path.parent.is_symlink()):
            raise MigrationError(f'{path}: symlink article or theme')
    scope = PublicationScope(repo)
    articles = sorted(scope.article_publication[1])
    claims_by_path = {p: [] for p in articles}
    origins = {p: [] for p in articles}
    changes = []
    theme_indexes = []
    anchor_cache = {}
    for idx in sorted(layer.glob('*/INDEX.md')):
        if idx.parent.name in EXCLUDED_DIRS or idx.parent.name.startswith('.'):
            continue
        if idx.is_symlink() or idx.parent.is_symlink():
            raise MigrationError(f'{idx}: symlink theme INDEX')
        theme_indexes.append(idx)
        for n, line in index_lines(idx):
            if not line.lstrip().startswith('- '):
                continue
            refs = local_references(idx, line)
            owners = {p for p, _ in refs if p in claims_by_path and p.parent == idx.parent}
            if not owners and idx in package_indexes:
                if any(not target.exists() for target, _ in refs):
                    raise MigrationError(f"{idx}:{n}: unresolved Package navigation")
                continue
            if len(owners) != 1:
                raise MigrationError(f'{idx}:{n}: claim must reference exactly one article in its theme')
            owner = next(iter(owners))
            for target, anchor in refs:
                if not target.exists():
                    raise MigrationError(f'{idx}:{n}: unresolved reference {target}')
                if target.is_relative_to(repo) and any(p.is_symlink() for p in [target, *target.parents] if p.is_relative_to(repo)):
                    raise MigrationError(f'{idx}:{n}: symlink reference {target}')
                if anchor and target.suffix in {'.md', '.py'} and target.is_relative_to(repo):
                    if target not in anchor_cache:
                        anchor_cache[target] = anchors(target)
                    if anchor not in anchor_cache[target]:
                        raise MigrationError(f'{idx}:{n}: unknown anchor {target}#{anchor}')
            # INDEX and article are siblings: relative links keep both their
            # spelling and referent. Fragment-only INDEX links are ambiguous.
            if local_references(owner, line) != refs:
                raise MigrationError(f'{idx}:{n}: references change when moved into {owner.name}')
            claims_by_path[owner].append(line.strip())
            origins[owner].append({'index': str(idx.relative_to(repo)), 'line': n})
    records = []
    for path in articles:
        before = path.read_bytes()
        old = read_article(path)
        claims = claims_by_path[path]
        if not claims:
            if not old.claims or old.error:
                raise MigrationError(f'{path}: no legacy or valid article-owned claims')
            claims = [c.text for c in old.claims]
        after = insert_claims(path, before.decode('utf-8'), claims).encode('utf-8')
        if before != after:
            changes.append(Change(path, before, after))
        selections = []
        for _, _, heading in headings(path):
            selected = next((c for c in claims if (path, slugify(heading)) in local_references(path, c)), claims[0])
            selections.append({'heading': heading, 'claim': selected})
        records.append({'path': str(path.relative_to(repo)), 'before_sha256': digest(before), 'after_sha256': digest(after),
                        'claims': claims, 'origins': origins[path], 'selections': selections})
    # An explicit Catalog Package INDEX remains an entrance; remove only its
    # claim bullets. Other theme INDEX files are retired, with navigation links
    # rewritten to the existing directory. No replacement inventory is made.
    deleted = set()
    for idx in theme_indexes:
        before = idx.read_bytes()
        if idx in package_indexes:
            claim_numbers = {o['line'] for items in origins.values() for o in items if o['index'] == str(idx.relative_to(repo))}
            after = ''.join(line for n, line in enumerate(before.decode().splitlines(keepends=True),1) if n not in claim_numbers).encode()
            if before != after:
                changes.append(Change(idx,before,after))
        else:
            deleted.add(idx)
            changes.append(Change(idx,before,None))
    for path in _repo_files(repo):
        if path in deleted:
            continue
        raw = path.read_text(encoding='utf-8')
        replacements = []
        # Match real links only, preserving code examples and all display text.
        lines = raw.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        for n,line in prose_lines(enumerate(lines,1)):
            offset = offsets[n-1]
            for m in LINK_RE.finditer(INLINE_CODE_RE.sub(lambda m: ' ' * len(m[0]),line)):
                target = urlsplit(m[1].strip('<>'))
                if target.scheme or target.netloc:
                    continue
                resolved = (path.parent/unquote(target.path)).resolve() if target.path else path
                if resolved not in deleted:
                    continue
                if target.fragment or path in articles or path.suffix != '.md':
                    raise MigrationError(f'{path}:{n}: navigation needs manual review before retiring {resolved}')
                value = m[1].replace('INDEX.md','')
                replacements.append((offset+m.start(1),offset+m.end(1),value))
        if replacements:
            for start,end,value in reversed(replacements):
                raw = raw[:start]+value+raw[end:]
            changes.append(Change(path,path.read_bytes(),raw.encode('utf-8')))
    if len({c.path for c in changes}) != len(changes):
        raise MigrationError('Overlapping article and navigation edits need review')
    changed = {c.path for c in changes}
    # Freeze every other article-layer file, including assets, maps and views.
    protected = {str(p.relative_to(repo)): digest(p.read_bytes()) for p in layer.rglob('*')
                 if p.is_file() and not p.is_symlink() and p not in changed}
    return Plan(repo, changes, records, protected)


def write_audit(plan: Plan, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=False)
    files = []
    for c in plan.changes:
        rel = c.path.relative_to(plan.repo)
        backup = directory/'before'/rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(c.before)
        files.append({'path':str(rel), 'before_sha256':digest(c.before),
                      'after_sha256':digest(c.after) if c.after is not None else None})
    audit = {'repo':str(plan.repo), 'status':'planned', **plan.summary(), 'files':files,
             'articles':plan.articles, 'protected':plan.protected}
    manifest = directory/'manifest.json'
    manifest.write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n')
    return manifest


def _replace(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(content)
        os.chmod(name,path.stat().st_mode & 0o777)
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def apply(plan: Plan, manifest: Path) -> None:
    # Check the entire read set again before any mutation. On an in-process
    # failure restore only our completed writes; a crash leaves the planned
    # audit/backups visible and verification detects the incomplete run.
    for c in plan.changes:
        if not os.access(c.path.parent, os.W_OK):
            raise MigrationError(f"{c.path.parent}: directory is not writable")
        if c.path.is_symlink() or c.path.read_bytes() != c.before:
            raise MigrationError(f'{c.path}: changed since planning')
    for rel, expected in plan.protected.items():
        if digest((plan.repo/rel).read_bytes()) != expected:
            raise MigrationError(f'{rel}: changed since planning')
    done = []
    try:
        for c in plan.changes:
            if c.after is None:
                c.path.unlink()
            else:
                _replace(c.path,c.after)
            done.append(c)
    except BaseException:
        for c in reversed(done):
            if c.after is None:
                if c.path.exists() or c.path.is_symlink():
                    raise MigrationError(f"{c.path}: concurrent recreation prevents rollback; inspect {manifest}")
                c.path.write_bytes(c.before)
            elif c.path.read_bytes() == c.after:
                _replace(c.path,c.before)
            else:
                raise MigrationError(f'{c.path}: concurrent edit prevents rollback; inspect {manifest}')
        raise
    data = json.loads(manifest.read_text())
    data['status'] = 'applied'
    manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    verify(plan.repo,manifest)


def verify(repo: Path, manifest: Path) -> dict:
    data = json.loads(manifest.read_text())
    if str(repo.resolve()) != data['repo']:
        raise MigrationError('Audit repository does not match --repo')
    for row in data['files']:
        path = repo / row['path']
        expected = row['after_sha256']
        if (expected is None and path.exists()) or (expected is not None and (not path.is_file() or digest(path.read_bytes()) != expected)):
            raise MigrationError(f'{path}: applied hash mismatch')
    for rel, expected in data['protected'].items():
        if not (repo/rel).is_file() or digest((repo/rel).read_bytes()) != expected:
            raise MigrationError(f'{rel}: protected content changed')
    scope=PublicationScope(repo)
    if {str(p.relative_to(repo)) for p in scope.article_publication[1]} != {r['path'] for r in data['articles']}:
        raise MigrationError('Published article membership changed')
    from .locators import current_locator
    for row in data['articles']:
        path = repo / row['path']
        article = read_article(path)
        if article.error or [c.text for c in article.claims] != row['claims']:
            raise MigrationError(f'{path}: claims differ')
        for selection in row['selections']:
            result=current_locator(path,scope=scope,section=selection['heading'])
            if result.get('claim') != selection['claim'] or result.get('claim_source',{}).get('path') != str(path):
                raise MigrationError(f'{path}: section selection differs')
    return {'status':'verified', 'articles':len(data['articles']), 'claims':sum(len(a['claims']) for a in data['articles']),
            'headings':sum(len(a['selections']) for a in data['articles'])}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--audit-dir',type=Path,required=True,help='Explicit store in the same trust boundary; each run gets a unique subdirectory')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--verify',type=Path,metavar='MANIFEST')
    args=parser.parse_args()
    try:
        if args.verify:
            print(json.dumps(verify(args.repo.resolve(), args.verify), ensure_ascii=False))
            return 0
        plan=build_plan(args.repo)
        audit=write_audit(plan,args.audit_dir/str(uuid.uuid4()))
        if args.apply:
            apply(plan,audit)
        print(json.dumps({'status':'applied' if args.apply else 'dry-run',**plan.summary(),'audit':str(audit)},ensure_ascii=False))
        return 0
    except (OSError, ValueError, SyntaxError) as exc:
        print(f'Migration stopped: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
