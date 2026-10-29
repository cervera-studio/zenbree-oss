import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vite-plus/test';

const root = path.join(import.meta.dirname, '..', '..');
// The two owners of the look: the primitives, and their phone-card variant.
const OWNERS = [
  path.join('components', 'ui', 'table.tsx'),
  path.join('components', 'ui', 'stacked-table.tsx'),
];
const MESSAGE =
  'Tables share one look from components/ui/table.tsx (StackedTable for phone cards): use the Table primitives and drop padding, text-size and header-background classes.';
// The one intended exception: a matrix of checkboxes squeezed on the phone.
const ALLOWED = {
  file: path.join('app', '(app)', 'settings', 'notifications', 'settings-form.tsx'),
  classes: 'phone:[&_td:not(:first-child)]:px-1 phone:[&_th:not(:first-child)]:px-1',
};

const RAW_TABLE = /<(table|thead|tbody|tfoot|tr|th|td)[\s>]/;
const TABLE_TAG =
  /<(Table|TableHeader|TableBody|TableHead|TableCell|TableRow|StackedTable)(?=[\s/>])/g;
const TOKEN_START = '(?<=^|[\\s"\'`])';
// Any variant chain before the utility: `desktop:`, `[&_th]:`, `*:`, `hover:`.
const VARIANTS = '(?:[^\\s"\'`]*:)?!?';
const END = '(?![\\w.-])';
const OVERRIDE = new RegExp(
  `${TOKEN_START}${VARIANTS}(?:p[xytrblse]?-[\\d.]+|text-(?:3xs|2xs|xs|xs-plus|sm|sm-plus|base|lg|xl|\\dxl)|bg-side)${END}`,
  'g',
);
const BACKGROUND = new RegExp(`${TOKEN_START}${VARIANTS}bg-[\\w/[\\]-]+`, 'g');
const STRING = '\'[^\']*\'|"[^"]*"|`[^`$]*`';
// `const X = 'a'` or `const X = cn('a', 'b')`: string literal arguments only.
const CONSTANT = new RegExp(
  `\\bconst\\s+(\\w+)\\s*=\\s*(?:cn\\(\\s*((?:${STRING})(?:\\s*,\\s*(?:${STRING}))*)\\s*,?\\s*\\)|(${STRING}))`,
  'g',
);
const LITERAL = new RegExp(STRING, 'g');
// A frame on the table itself; a wrapper div's frame is not checked.
const FRAME = new RegExp(
  `${TOKEN_START}${VARIANTS}(?:border(?!-(?:collapse|separate|spacing))|rounded)(?:-[\\w[\\]./-]+)?${END}`,
  'g',
);
// A background aimed at the header from the table or a row: `[&_thead]:bg-*`, `[&_th]:bg-*`, `[&_tr]:bg-*`.
const HEADER_BG = new RegExp(`${TOKEN_START}[^\\s"'\`]*\\[&[^\\s"'\`]*\\]:!?bg-[\\w/[\\]-]+`, 'g');
const CLASS_NAME = /\bclassName=/g;
// `//` starts a comment only after whitespace or punctuation, never inside `https://`.
const LINE_COMMENT = /(?<=^|[\s;{}),])\/\/.*$/gm;
const BLOCK_COMMENT = /\/\*[\s\S]*?\*\//g;

function* sources(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === '.next') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* sources(full);
    else if (entry.name.endsWith('.tsx')) yield full;
  }
}

function tagText(source: string, start: number): string {
  let depth = 0;
  let quote = '';
  for (let i = start; i < source.length; i++) {
    const c = source[i];
    if (quote) {
      if (c === quote) quote = '';
    } else if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth++;
    else if (c === '}') depth--;
    else if (c === '>' && depth === 0) return source.slice(start, i);
  }
  return source.slice(start);
}

/** An attribute value at the start of `rest`: a quoted string or a balanced `{…}` expression. */
function attributeValue(rest: string): string {
  const open = rest[0];
  if (open === '"' || open === "'") return rest.slice(0, rest.indexOf(open, 1) + 1);
  let depth = 0;
  let quote = '';
  for (let i = 0; i < rest.length; i++) {
    const c = rest[i];
    if (quote) {
      if (c === quote) quote = '';
    } else if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth++;
    else if (c === '}' && --depth === 0) return rest.slice(0, i + 1);
  }
  return rest;
}

/** The value of every `className=` attribute in a tag. */
function classNames(tag: string): string {
  let out = '';
  for (const m of tag.matchAll(CLASS_NAME))
    out += ` ${attributeValue(tag.slice(m.index + m[0].length))}`;
  return out;
}

/** The tag's className plus the same-file string constants named inside it, so `className={CELL}` is scanned. */
function expandConstants(tag: string, source: string): string {
  const expression = classNames(tag);
  let text = tag;
  for (const [, name, args, single] of source.matchAll(CONSTANT)) {
    if (!new RegExp(`\\b${name}\\b`).test(expression)) continue;
    text += ` ${(args ?? single ?? '')
      .match(LITERAL)
      ?.map((s) => s.slice(1, -1))
      .join(' ')}`;
  }
  return text;
}

function stripComments(source: string): string {
  return source.replace(BLOCK_COMMENT, ' ').replace(LINE_COMMENT, '');
}

function headerRanges(source: string): [number, number][] {
  return [...source.matchAll(/<TableHeader(?=[\s/>])/g)].map((m) => {
    const end = source.indexOf('</TableHeader>', m.index);
    return [m.index, end === -1 ? source.length : end];
  });
}

function tagHits(name: string, text: string, inHeader: boolean): Set<string> {
  const hits = new Set(text.match(OVERRIDE));
  const add = (re: RegExp) => {
    for (const hit of text.match(re) ?? []) hits.add(hit);
  };
  if (name === 'TableHeader' || name === 'TableHead' || (name === 'TableRow' && inHeader)) {
    add(BACKGROUND);
  }
  if (name === 'Table' || name === 'StackedTable' || name === 'TableRow') add(HEADER_BG);
  if (name === 'Table' || name === 'StackedTable') add(FRAME);
  return hits;
}

function scan(rel: string, raw: string): string[] {
  if (OWNERS.includes(rel)) return [];
  const source = stripComments(raw);
  const found: string[] = [];
  if (RAW_TABLE.test(source)) found.push(`${rel}: raw table JSX`);
  const headers = headerRanges(source);
  for (const match of source.matchAll(TABLE_TAG)) {
    let text = expandConstants(tagText(source, match.index), source);
    if (rel === ALLOWED.file) text = text.replace(ALLOWED.classes, '');
    const inHeader = headers.some(([from, to]) => match.index >= from && match.index < to);
    const hits = tagHits(match[1] ?? '', text, inHeader);
    if (hits.size) found.push(`${rel}: <${match[1]}> ${[...hits].join(' ')}`);
  }
  return found;
}

describe('tables', () => {
  it('share one look', () => {
    const found: string[] = [];
    for (const dir of ['app', 'components', 'lib']) {
      for (const file of sources(path.join(root, dir))) {
        found.push(...scan(path.relative(root, file), readFileSync(file, 'utf8')));
      }
    }
    expect(found, MESSAGE).toEqual([]);
  });
});

describe('the scanner', () => {
  const caught: [string, string][] = [
    [
      'a same-file constant in className',
      `const CELL = 'px-3.5';\n<TableCell className={CELL}>x</TableCell>`,
    ],
    [
      'a constant inside a template',
      "const CELL = 'py-2';\n<TableCell className={`$" + '{CELL} font-bold`}>x</TableCell>',
    ],
    [
      'a constant inside cn()',
      "const CELL = 'text-xs';\n<TableCell className={cn(CELL, 'a')}>x</TableCell>",
    ],
    ['a child variant', '<Table className="[&_th]:px-3.5">x</Table>'],
    ['a child variant on the cell', '<Table className="[&_td]:py-2.5">x</Table>'],
    ['a child variant on the text size', '<Table className="[&_th]:text-xs">x</Table>'],
    ['a breakpoint variant', '<TableCell className="desktop:py-4">x</TableCell>'],
    ['a universal child variant', '<TableRow className="*:px-4">x</TableRow>'],
    ['a hover header background', '<TableRow className="hover:bg-side">x</TableRow>'],
    ['a TableHeader', '<TableHeader className="px-2">x</TableHeader>'],
    ['a TableBody', '<TableBody className="text-sm">x</TableBody>'],
    ['a TableHeader background', '<TableHeader className="bg-ground">x</TableHeader>'],
    ['a TableHead background', '<TableHead className="bg-raised">x</TableHead>'],
    ['logical padding', '<TableCell className="ps-3">x</TableCell>'],
    ['a large text size', '<TableCell className="text-lg">x</TableCell>'],
    ['a numbered large text size', '<TableCell className="text-2xl">x</TableCell>'],
    ['a theme text size', '<TableCell className="text-xs-plus">x</TableCell>'],
    [
      'a header row background',
      '<TableHeader><TableRow className="bg-raised/50">x</TableRow></TableHeader>',
    ],
    ['a thead background from the table', '<Table className="[&_thead]:bg-raised">x</Table>'],
    ['a th background from the table', '<Table className="hover:[&_th]:bg-raised/40">x</Table>'],
    ['a tr background from the table', '<Table className="[&_tr]:bg-ground">x</Table>'],
    ['a border on the table', '<Table className="border">x</Table>'],
    ['a border side on the table', '<Table className="border-b">x</Table>'],
    ['rounded corners on the table', '<Table className="rounded-lg">x</Table>'],
    ['a frame on a stacked table', '<StackedTable className="border rounded-md">x</StackedTable>'],
    [
      'a cn() constant',
      "const CELL = cn('font-mono', 'px-3');\n<TableCell className={CELL}>x</TableCell>",
    ],
    ['the allowed classes elsewhere', `<Table className="${ALLOWED.classes}">x</Table>`],
  ];
  it.each(caught)('catches %s', (_name, source) => {
    expect(scan('app/x.tsx', source)).not.toEqual([]);
  });

  it('passes a plain table', () => {
    const source = `const CELL = 'font-mono';
<Table className="min-w-240"><TableHeader><TableRow><TableHead data-numeric>a</TableHead></TableRow></TableHeader>
<TableBody><TableRow className="hover:bg-raised/50"><TableCell className={CELL} data-numeric>b</TableCell></TableRow></TableBody></Table>`;
    expect(scan('app/x.tsx', source)).toEqual([]);
  });

  const clean: [string, string][] = [
    [
      'a constant named only outside className',
      'const id = \'p-4\';\n<TableRow id={id} className="hover:bg-raised/50">x</TableRow>',
    ],
    [
      'a constant named after className',
      'const id = \'p-4\';\n<TableRow className="hover:bg-raised/50" id={id}>x</TableRow>',
    ],
    ['a table-layout border utility', '<Table className="border-collapse">x</Table>'],
    [
      'a row body background outside the header',
      '<TableBody><TableRow className="bg-raised/50">x</TableRow></TableBody>',
    ],
    ['a raw table in a line comment', '// <td> was here\n<Table>x</Table>'],
    ['a raw table in a block comment', '/* <tr> old */\n<Table>x</Table>'],
    ['a raw table in a JSX comment', '<Table>{/* <th> old */}x</Table>'],
    ['a wrapper frame', '<div className="rounded-lg border"><Table>x</Table></div>'],
  ];
  it.each(clean)('passes %s', (_name, source) => {
    expect(scan('app/x.tsx', source)).toEqual([]);
  });

  it('allows the notifications matrix classes in its own file only', () => {
    const source = `<Table className="${ALLOWED.classes}">x</Table>`;
    expect(scan(ALLOWED.file, source)).toEqual([]);
  });
});

describe('the frame on a stacked table', () => {
  const source = readFileSync(path.join(root, 'components', 'ui', 'table.tsx'), 'utf8');
  const classes = (name: string) =>
    [
      ...(source.match(new RegExp(`const ${name} = cn\\(([\\s\\S]*?)\\n\\);`))?.[1] ?? '').matchAll(
        /'([^']*)'/g,
      ),
    ]
      .flatMap((m) => (m[1] ?? '').split(' '))
      .filter(Boolean);

  it('has every rule of the data-table frame, from desktop only', () => {
    const framed = classes('FRAMED');
    expect(framed.length).toBeGreaterThan(0);
    expect(classes('FRAMED_FROM_DESKTOP')).toEqual(framed.map((c) => `desktop:${c}`));
  });

  it('has the box of the plain frame, from desktop only', () => {
    const box = (name: string) =>
      (source.match(new RegExp(`const ${name} =\\s*'([^']*)'`))?.[1] ?? '').split(' ');
    expect(box('FRAME_BOX').length).toBeGreaterThan(1);
    expect(box('FRAME_BOX_FROM_DESKTOP')).toEqual(box('FRAME_BOX').map((c) => `desktop:${c}`));
  });
});
