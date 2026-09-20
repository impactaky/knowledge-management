"""Catalog entrances do not control publication or require navigation indexes."""
import asyncio
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

FOUNDATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FOUNDATION / 'tools/knowledge-ui'))
import check as knowledge_check
import indexer
import server
from federation_core import core


def files_in_tree(nodes):
    return {node['file'] for node in nodes if node['type'] == 'file'} | {
        path for node in nodes if node['type'] == 'dir'
        for path in files_in_tree(node['children'])
    }


class OptionalPackageIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalog = self.write('CATALOG.md', '## パッケージ\n- [papers](articles/) — Papers\n- [rules](rules/tooling.md) — Rules\n')
        self.article = self.write('articles/theme/article.md', '---\nclaims: ["Authored claim"]\n---\n# Article\n## Finding\nPUBLIC_BODY\n')
        self.rule = self.write('rules/tooling.md', '# Tooling\nDIRECT_RULE_BODY\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def search(self, query):
        with patch.object(core, '_meili_request', side_effect=RuntimeError('offline fixture')):
            return core.search(query, catalog_path=self.catalog)

    def grep(self, query):
        return core.grep(query, catalog_path=self.catalog)

    def tree(self):
        with patch.object(server, 'CATALOG_PATH', self.catalog):
            return asyncio.run(server.tree())['packages']

    def test_directory_without_index_and_direct_file_check_tree_and_read(self):
        self.assertEqual(knowledge_check.check(self.catalog), [])
        packages = indexer.parse_catalog(self.catalog)
        self.assertEqual(packages[0]['entry_path'], self.root / 'articles')
        self.assertEqual(packages[1]['root'], self.root / 'rules')
        tree = self.tree()
        self.assertEqual([p['entryFile'] for p in tree], ['', str(self.rule)])
        self.assertIn(str(self.article), files_in_tree(tree[0]['children']))
        with patch.object(server, 'CATALOG_PATH', self.catalog):
            self.assertIn(b'DIRECT_RULE_BODY', asyncio.run(server.article(str(self.rule), embed=True)).body)
        result = self.search('DIRECT_RULE_BODY')
        self.assertEqual(result['index_results'], [])
        self.assertEqual(result['fulltext_results'], [])
        result = self.grep('DIRECT_RULE_BODY')
        self.assertEqual([r['path'] for r in result['fulltext_results']], [str(self.rule)])
        self.assertNotIn('DIRECT_RULE_BODY', json.dumps(result['fulltext_results']))

    def test_retained_index_is_searchable_but_not_an_implicit_entrance(self):
        index = self.write('articles/INDEX.md', '- RETAINED_RULE\n')
        self.write('rules/CONTEXT.md', '**Vocabulary**:\nTERM_DEFINITION\n')
        self.assertEqual(self.tree()[0]['entryFile'], '')
        self.assertEqual([r['path'] for r in self.search('RETAINED_RULE')['index_results']], [str(index)])
        packages = indexer.parse_catalog(self.catalog)
        self.assertTrue(indexer.collect_index_entries(packages[0]))
        self.assertTrue(indexer.collect_term_entries(packages[1]))

    def test_cached_and_current_clients_recover_from_deleted_last_file(self):
        old_last_file = str(self.root / 'articles/INDEX.md')
        self.assertFalse(Path(old_last_file).exists())
        index = self.write('rules/INDEX.md', '# Optional navigation\n')
        cases = []
        for target, expected in (
            ('rules/tooling.md', str(self.rule)),
            ('rules/INDEX.md', str(index)),
            ('rules/', ''),  # Even a directory containing INDEX is not a file entrance.
        ):
            self.catalog.write_text(
                '## パッケージ\n- [papers](articles/) — Papers\n'
                f'- [rules]({target}) — Rules\n'
            )
            packages = self.tree()
            cases.append({'packages': packages, 'expected': expected})
            with self.subTest(target=target):
                for field in ('entryFile', 'indexFile'):
                    self.assertEqual([p.get(field) for p in packages], ['', expected])

        html = (FOUNDATION / 'tools/knowledge-ui/static/index.html').read_text()
        functions = '\n'.join(
            re.search(rf'^function {name}\([^\n]*\).*?^}}', html, re.M | re.S).group()
            for name in ('openInitialDocument', 'findLabelForFile', 'findNode', 'isDraftFile')
        )
        # Preserve the pre-entryFile client, including its deleted-lastFile fallback.
        legacy = '''
function openLegacyInitialDocument() {
  const lastFile = localStorage.getItem("knowledgeBrowser:lastFile") || "";
  const firstIndex = state.packages.find((pkg) => pkg.indexFile)?.indexFile || "";
  const lastLabel = lastFile ? findLabelForFile(lastFile) : "";
  const file = lastLabel ? lastFile : firstIndex;
  if (!file) return;
  const label = lastLabel || findLabelForFile(file) || file;
  if (lastLabel && isDraftFile(file)) setTab("drafts");
  openDoc(file, "", label);
}
'''
        script = functions + legacy + '''
let state, opened;
const localStorage = {getItem: () => OLD_LAST_FILE};
function openDoc(file) { opened.push(file); }
function setTab() { throw new Error("Deleted article is not a draft"); }
for (const scenario of CASES) {
  state = {packages: scenario.packages, drafts: {children: []}, draftsLoaded: true};
  for (const initialize of [openLegacyInitialDocument, openInitialDocument]) {
    opened = [];
    initialize();
    const expected = scenario.expected ? [scenario.expected] : [];
    if (JSON.stringify(opened) !== JSON.stringify(expected)) {
      throw new Error(`${initialize.name}: ${JSON.stringify(opened)} != ${JSON.stringify(expected)}`);
    }
  }
}
console.log("Legacy/current initialization: 6 scenarios passed");
'''
        script = f'const OLD_LAST_FILE = {json.dumps(old_last_file)};\nconst CASES = {json.dumps(cases)};\n' + script
        if not shutil.which('deno'):
            self.skipTest('deno not found')
        result = subprocess.run(
            ['deno', 'run', '--no-config', '-'], input=script,
            text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_file_entrance_leaves_both_api_fields_empty(self):
        self.rule.unlink()
        for package in self.tree():
            self.assertEqual(package['entryFile'], '')
            self.assertEqual(package.get('indexFile'), '')

    def test_publication_exclusions_and_maps_without_index(self):
        maps = [self.write(f'articles/{kind}/map.md', 'PRIVATE_BODY\n') for kind in ('books', 'studies')]
        for directory in ('drafts', 'tests', 'assets', 'worklogs', '__marimo__'):
            self.write(f'articles/theme/{directory}/hidden.md', 'PRIVATE_BODY\n')
        outside = self.write('outside.md', 'PRIVATE_BODY\n')
        (self.article.parent / 'leak.md').symlink_to(outside)
        packages = indexer.parse_catalog(self.catalog)
        chunks = indexer.collect_catalog_article_chunks(packages)
        self.assertEqual({d['file'] for d in chunks}, {str(self.article)})
        self.assertEqual(len(indexer.collect_claim_entries(packages[0])), 1)
        self.assertEqual(len(self.search('Authored claim')['index_results']), 1)
        self.assertEqual([r['path'] for r in self.grep('PUBLIC_BODY')['fulltext_results']], [str(self.article)])
        self.assertEqual(self.grep('PRIVATE_BODY')['fulltext_results'], [])
        private = self.search('PRIVATE_BODY')
        self.assertFalse(any(private[key] for key in ('index_results', 'article_results', 'fulltext_results')))
        self.assertTrue({str(p) for p in maps} <= files_in_tree(self.tree()[0]['children']))
        self.assertEqual(knowledge_check.check(self.catalog), [])

    def test_missing_entrances_and_scope_findings_are_both_reported(self):
        self.rule.unlink()
        self.write('rules/broken.md', '[Broken](absent.md)\n')
        self.catalog.write_text(self.catalog.read_text() + '- [absent](missing.dir/) — Missing directory\n')
        findings = knowledge_check.check(self.catalog)
        self.assertEqual({f.path for f in findings if f.code == 'missing-entry'}, {self.rule, self.root / 'missing.dir'})
        self.assertIn('missing-link', [f.code for f in findings])
        self.assertEqual(self.tree()[1]['entryFile'], '')
        with patch.object(indexer, 'setup_entries_index') as entries:
            with self.assertRaisesRegex(ValueError, 'Catalog entry'):
                indexer.main(self.catalog, 'http://fixture')
        entries.assert_not_called()

    def test_directory_target_cannot_be_replaced_by_a_file(self):
        self.write('not-a-directory.md', '# File\n')
        self.catalog.write_text('## パッケージ\n- [wrong](not-a-directory.md/) — Directory required\n')
        self.assertEqual([f.code for f in knowledge_check.check(self.catalog)], ['missing-entry'])
        self.assertEqual(self.tree()[0]['entryFile'], '')

    def test_duplicates_are_rejected_by_all_consumers(self):
        for entry in ('- [papers](rules/) — Duplicate name', '- [other](articles/CONTEXT.md) — Duplicate root'):
            with self.subTest(entry=entry):
                self.catalog.write_text('## パッケージ\n- [papers](articles/) — Papers\n' + entry + '\n')
                for parse in (core.parse_catalog, indexer.parse_catalog, server.parse_catalog_packages):
                    with self.assertRaisesRegex(ValueError, 'duplicate package name or root'):
                        parse(self.catalog)
                self.assertEqual([f.code for f in knowledge_check.check(self.catalog)], ['catalog'])

    def test_external_nested_roots_keep_ownership_and_exclusions(self):
        # The external store is synthetic and outside the Catalog directory.
        local = self.root / 'local'
        local.mkdir()
        external_article = self.write('external/articles/topic/article.md', '---\nclaims: ["External claim"]\n---\n# External\nEXTERNAL_BODY\n')
        self.write('external/worklogs/secret.md', 'PRIVATE_BODY\n')
        self.catalog = self.write('local/CATALOG.md', f'## パッケージ\n- [company]({self.root}/external/) — Synthetic company\n- [topic]({external_article.parent}/) — Nested\n- [private]({self.root}/external/worklogs/) — Excluded\n')
        self.assertEqual(knowledge_check.check(self.catalog), [])
        chunks = indexer.collect_catalog_article_chunks(indexer.parse_catalog(self.catalog))
        self.assertEqual({(d['file'], d['package']) for d in chunks}, {(str(external_article), 'topic')})
        hits = self.grep('EXTERNAL_BODY')['fulltext_results']
        self.assertEqual([(h['path'], h['package']) for h in hits], [(str(external_article), 'topic')])
        self.assertEqual(self.grep('PRIVATE_BODY')['fulltext_results'], [])
        outer, inner, _ = self.tree()
        self.assertNotIn(str(external_article), files_in_tree(outer['children']))
        self.assertIn(str(external_article), files_in_tree(inner['children']))

    def test_fingerprint_and_live_hits_follow_entrance_changes_and_deletion(self):
        first = indexer.corpus_fingerprint(self.catalog)
        self.rule.write_text('# Updated\nCHANGED_RULE\n')
        second = indexer.corpus_fingerprint(self.catalog)
        self.assertNotEqual(first, second)
        self.rule.unlink()
        self.assertNotEqual(second, indexer.corpus_fingerprint(self.catalog))
        self.assertEqual(self.grep('CHANGED_RULE')['fulltext_results'], [])
        index = self.write('articles/INDEX.md', '- Old navigation\n')
        before = indexer.corpus_fingerprint(self.catalog)
        index.unlink()
        self.assertNotEqual(before, indexer.corpus_fingerprint(self.catalog))

        def cached(_url, _method, endpoint, payload=None):
            if endpoint == '/health':
                return {'status': 'available'}
            if endpoint.endswith('/entries/search'):
                return {'hits': [{'file': str(index), 'text': '- Old navigation', 'line': 1}]}
            return {'hits': [{'file': str(self.article), 'section': 'Finding', '_rankingScore': .9}]}

        with patch.object(core, '_meili_request', side_effect=cached):
            result = core.search('unrelated', catalog_path=self.catalog, meili_url='http://fixture')
        self.assertEqual(result['index_results'], [])
        self.assertEqual([r['path'] for r in result['article_results']], [str(self.article)])
        self.assertEqual(result['article_results'][0]['claim'], 'Authored claim')
        before = indexer.corpus_fingerprint(self.catalog)
        self.catalog.write_text('## パッケージ\n- [papers](articles/theme/article.md) — Direct article\n')
        self.assertNotEqual(before, indexer.corpus_fingerprint(self.catalog))
        self.assertEqual(core.parse_catalog(self.catalog)[0].root, self.article.parent)


if __name__ == '__main__':
    unittest.main()
