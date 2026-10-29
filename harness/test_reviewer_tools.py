"""The harness-reviewer agent reads only: no Bash, so a planted instruction in a PR has nothing to run."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ReviewerToolsTest(unittest.TestCase):
    def test_reviewer_has_only_read_tools(self):
        frontmatter = (ROOT / '.claude/agents/harness-reviewer.md').read_text().split('---')[1]
        (tools,) = re.findall(r'^tools:(.*)$', frontmatter, re.MULTILINE)
        self.assertLessEqual({t.strip() for t in tools.split(',')}, {'Read', 'Glob', 'Grep'})
        self.assertNotIn('hooks:', frontmatter)  # a tool list without Bash needs no hook


if __name__ == '__main__':
    unittest.main()
