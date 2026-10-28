"""Which commits the fixes on main repair.

For each fix commit on main's first-parent history, blame the source lines it removed or changed (tests excluded)
and name the commit that wrote most of them: a feature, an earlier fix, or another kind. On 2026-10-12 it found
125 fix commits: 80 repaired lines a feature wrote, 16 repaired an earlier fix, 19 changed no source line, and the
median age of a repaired line was 45 h (#659). It reads only git history and writes one TSV row per fix commit.
Usage: python3 harness/fix_census.py OUT.tsv [--base REF]   (default: main)
"""

import argparse
import re
import statistics
import subprocess
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = re.compile(r'^(api/app|web/(app|components|lib|i18n|messages))/.*\.(py|ts|tsx|json)$')
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? ', re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.run(['git', *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def is_source(path: str) -> bool:
    return bool(SOURCE.match(path)) and 'test' not in path


def kind(subject: str) -> str:
    """The kind of the commit that wrote the repaired lines, by its Conventional Commit type."""
    if subject.startswith('fix'):
        return 'fix-of-fix'
    if re.match(r'(feat|M1|refactor|perf)', subject):
        return 'feat/initial'
    return 'other:' + subject.split(':')[0][:12]


def authors(sha: str) -> Counter[str]:
    """Count, by the commit that wrote them, the source lines ``sha`` removed or changed."""
    found: Counter[str] = Counter()
    for path in git('diff', '--name-only', f'{sha}^', sha).split():
        if not is_source(path):
            continue
        for hunk in HUNK.finditer(git('diff', '-U0', f'{sha}^', sha, '--', path)):
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            if count == 0:  # a pure addition repairs no line
                continue
            blame = git('blame', '-l', '-s', '-L', f'{start},{start + count - 1}', f'{sha}^', '--', path)
            found.update(line.split()[0].lstrip('^') for line in blame.splitlines())
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('out', type=Path)
    parser.add_argument('--base', default='main')
    args = parser.parse_args()

    history = [line.split(' ', 2) for line in git('log', '--format=%H %ct %s', args.base).splitlines()]
    subject = {sha: s for sha, _, s in history}
    time = {sha: int(t) for sha, t, _ in history}
    fixes = [line.split(' ', 1) for line in git('log', '--first-parent', '--format=%H %s', args.base).splitlines()]

    rows = []
    for sha, fix_subject in fixes:
        if not fix_subject.startswith('fix'):
            continue
        written = authors(sha)
        if not written:
            rows.append((sha, fix_subject, 'no-src-lines', '', None))
            continue
        author = written.most_common(1)[0][0]  # a boundary commit's id loses one character to its ^
        author = next((full for full in subject if full.startswith(author)), author)
        age_h = (time[sha] - time.get(author, time[sha])) / 3600
        rows.append((sha, fix_subject, kind(subject.get(author, '?')), subject.get(author, '?'), age_h))

    print('fix commits', len(rows))
    print(Counter(row[2] for row in rows).most_common())
    if ages := [row[4] for row in rows if row[4] is not None]:
        print('median age of repaired lines (h)', round(statistics.median_high(ages), 1))
    with args.out.open('w') as out:
        for sha, fix_subject, row_kind, author_subject, age_h in rows:
            age = '' if age_h is None else round(age_h, 1)
            out.write(f'{sha[:8]}\t{row_kind}\t{age}\t{fix_subject[:80]}\t{author_subject[:80]}\n')


if __name__ == '__main__':
    main()
