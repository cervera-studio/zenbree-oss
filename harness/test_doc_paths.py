"""The recipes and per-area AGENTS files point at repo paths. A path that moved must fail here, not mislead an agent."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = ('docs/recipes.md', 'docs/migrations.md', 'api/AGENTS.md', 'landing/AGENTS.md')
ROOTS = ('api/', 'web/', 'landing/', 'docs/', 'harness/', 'ops/', '.claude/', 'dev/')


def named_paths(text):
    for token in re.findall(r'`([^`\n]+)`', text):
        token = token.split('::')[0].split(' ')[0].rstrip('.,:;')
        if token.startswith(ROOTS) and not re.search(r'[<>{}*$…]|\.\.\.', token):
            yield token


class DocPathsExist(unittest.TestCase):
    def test_every_path_a_doc_names_exists(self):
        missing = [f'{doc}: {path}' for doc in DOCS for path in sorted(set(named_paths((REPO / doc).read_text())))
                   if not (REPO / path.rstrip('/')).exists()]
        self.assertEqual(missing, [], 'these docs name paths that do not exist; fix the doc or the path')

    def test_the_scan_finds_paths(self):
        # Guards the regex: a scan that finds nothing would pass the test above for any doc.
        self.assertGreater(len(set(named_paths((REPO / 'docs/recipes.md').read_text()))), 30)


if __name__ == '__main__':
    unittest.main()
