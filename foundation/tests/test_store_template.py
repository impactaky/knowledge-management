"""Separate stores and copied skills keep their upstream references portable."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / 'foundation/tools/knowledge-ui/check.py'


def test_template_checker_advisory(tmp_path):
    store = tmp_path / 'store'
    shutil.copytree(ROOT / 'templates/store', store)
    catalog = store / 'CATALOG.md'
    content = catalog.read_text().replace('/path/to/knowledge-management/foundation/INDEX.md', str(ROOT / 'foundation/INDEX.md'))
    catalog.write_text(content)
    def run():
        return subprocess.run([sys.executable, str(CHECKER), '--catalog', str(catalog)], capture_output=True, text=True)
    present = run()
    assert present.returncode == 0, present.stdout + present.stderr
    assert 'Knowledge check: 0 issue(s)' in present.stdout
    assert 'Advisory:' not in present.stdout
    # Any name resolving to this checkout's foundation satisfies the advisory.
    catalog.write_text(content.replace('[shared-rules]', '[rules]'))
    assert 'Advisory:' not in run().stdout
    catalog.write_text('\n'.join(line for line in content.splitlines() if not line.startswith('- [shared-rules]')) + '\n')
    missing = run()
    assert missing.returncode == present.returncode
    assert 'Knowledge check: 0 issue(s)' in missing.stdout
    assert f'- [shared-rules]({ROOT / "foundation/INDEX.md"})' in missing.stdout
    assert 'Advisory:' in missing.stdout


def test_checkout_catalogs_have_no_advisory():
    for catalog in (ROOT / 'CATALOG.md', ROOT / 'examples/minimal/CATALOG.md'):
        result = subprocess.run([sys.executable, str(CHECKER), '--catalog', str(catalog)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Advisory:' not in result.stdout


def test_skill_markdown_links_stay_within_skill():
    for source in (ROOT / 'skills').rglob('*.md'):
        skill = ROOT / 'skills' / source.relative_to(ROOT / 'skills').parts[0]
        text = source.read_text()
        # Inline links/images and reference-style definitions, including examples.
        targets = re.findall(r'!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)', text)
        targets += re.findall(r'^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)', text, re.M)
        for target in targets:
            url = urlsplit(target.strip('<>'))
            if url.scheme not in ('', 'file') or url.netloc:
                continue
            resolved = (source.parent / unquote(url.path)).resolve() if url.path else source.resolve()
            assert resolved.is_relative_to(skill.resolve()), (source, target)
