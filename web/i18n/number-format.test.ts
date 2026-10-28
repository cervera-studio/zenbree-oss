import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vite-plus/test';

// Italian Intl drops the thousands separator on 4-digit numbers, so a count formatted with the
// defaults reads «1234» next to «12.345». Every count in the UI names a format from
// `formats.number` in i18n/request.ts, which sets the grouping once.

const web = path.join(import.meta.dirname, '..');
const SKIP = new Set(['node_modules', '.next', 'coverage']);

function* files(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (SKIP.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* files(full);
    else yield full;
  }
}

const rel = (file: string) => path.relative(web, file).split(path.sep).join('/');
const sources = ['app', 'components', 'lib'].flatMap((dir) =>
  [...files(path.join(web, dir))].filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f)),
);

/** Arguments of the call whose `(` is at `open`, split on top-level commas. */
function callArgs(text: string, open: number): string[] {
  const args: string[] = [];
  let depth = 0;
  let start = open + 1;
  for (let i = open + 1; i < text.length; i++) {
    const ch = text.charAt(i);
    if ('([{'.includes(ch)) depth++;
    else if (')]}'.includes(ch)) {
      if (depth === 0) return [...args, text.slice(start, i)].filter((a) => a.trim() !== '');
      depth--;
    } else if (ch === ',' && depth === 0) {
      args.push(text.slice(start, i));
      start = i + 1;
    }
  }
  return args;
}

const line = (text: string, index: number) => text.slice(0, index).split('\n').length;

describe('number formatting', () => {
  it('message placeholders name a number format', () => {
    const bare = [...files(path.join(web, 'messages'))]
      .filter((f) => f.endsWith('.json'))
      .flatMap((f) =>
        [...readFileSync(f, 'utf8').matchAll(/\{\s*\w+\s*,\s*number\s*\}/g)].map(
          (m) => `${rel(f)}: ${m[0]}`,
        ),
      );
    expect(bare, 'write {n, number, credits} (or another format from i18n/request.ts)').toEqual([]);
  });

  it('formatter calls name a number format', () => {
    const unnamed = sources.flatMap((f) => {
      const text = readFileSync(f, 'utf8');
      if (!/\b(?:useFormatter|getFormatter)\b/.test(text)) return [];
      return [...text.matchAll(/\b\w+\.number\(/g)]
        .filter((m) => callArgs(text, (m.index ?? 0) + m[0].length - 1).length < 2)
        .map((m) => `${rel(f)}:${line(text, m.index ?? 0)}`);
    });
    expect(unnamed, "pass a format from i18n/request.ts: format.number(n, 'credits')").toEqual([]);
  });

  // A formatted string reaches a plain {arg}, where the typecheck cannot ask for a plural:
  // «Tetto: 1 crediti al mese».
  it('messages get numbers, not formatted strings', () => {
    const preformatted = sources.flatMap((f) => {
      const text = readFileSync(f, 'utf8');
      return [...text.matchAll(/\b(?:t[A-Za-z]{0,4}|errors)(?:\.rich|\.markup)?\(\s*['`]/g)]
        .filter((m) =>
          callArgs(text, (m.index ?? 0) + m[0].indexOf('('))
            .slice(1)
            .some((arg) => /\b\w+:\s*\w+\.number\(/.test(arg)),
        )
        .map((m) => `${rel(f)}:${line(text, m.index ?? 0)}`);
    });
    expect(
      preformatted,
      "pass the number and format it in the message: t('k', { n }) with {n, plural, one {# credito} other {{n, number, credits} crediti}}",
    ).toEqual([]);
  });

  it('components and pages format numbers through next-intl', () => {
    const raw = sources
      .filter((f) => !rel(f).startsWith('lib/'))
      .flatMap((f) => {
        const text = readFileSync(f, 'utf8');
        return [...text.matchAll(/new Intl\.NumberFormat\b/g)].map(
          (m) => `${rel(f)}:${line(text, m.index ?? 0)}`,
        );
      });
    expect(
      raw,
      "use useFormatter()/getFormatter() with a named format (format.number(n, 'credits')) or a lib/ formatter",
    ).toEqual([]);
  });
});
