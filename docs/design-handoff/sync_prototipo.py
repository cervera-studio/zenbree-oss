"""Sync docs/design-handoff/prototipo from a downloaded Claude Design artifact.

usage: sync_prototipo.py <artifact dir with project/ and artifact-type/> <prototipo dir> [--index-only]
"""
import html
import json
import re
import shutil
import sys
from pathlib import Path

FONT_LINK = re.compile(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com/[^"]*">')
LOCAL_FONTS = '<link rel="stylesheet" href="./fonts.css">'


def index_html(canvas: dict, out: Path, head: str, tail: str) -> str:
    boards = canvas['boards']
    files = {p.name for p in out.glob('*.dc.html')}
    sections = []
    for page in canvas['pages']:
        items = []
        for name in canvas['order']:
            b = boards.get(name)
            if not b or b['page'] != page['id'] or name.startswith('Dark-'):
                continue
            title = html.escape(b.get('title', name), quote=True)
            item = f'<li><a href="{name}">{title}</a>'
            if f'Dark-{name}' in files:
                item += f' <a class="dk" href="Dark-{name}">scuro</a>'
            items.append(item + '</li>')
        sections.append(f'<section><h2>{html.escape(page["name"])}</h2><ul>{"".join(items)}</ul></section>')
    return head + '<div class="grid">' + ''.join(sections) + '</div>' + tail


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    canvas = json.loads((src / 'project/canvas.json').read_text())
    if '--index-only' not in sys.argv:
        for f in sorted((src / 'project').glob('*.dc.html')):
            text = f.read_text()
            new, n = FONT_LINK.subn(LOCAL_FONTS, text)
            assert n == 1, f'{f.name}: expected one Google Fonts link, found {n}'
            (out / f.name).write_text(new)
        shutil.copyfile(src / 'artifact-type/dc-runtime.js', out / 'support.js')
    current = (out / 'index.html').read_text()
    head = current[: current.index('<div class="grid">')]
    tail = current[current.index('</div>\n</main>') + len('</div>') :]
    (out / 'index.html').write_text(index_html(canvas, out, head, tail))


main()
