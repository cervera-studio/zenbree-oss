import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { compile } from 'tailwindcss';
import { describe, expect, it } from 'vite-plus/test';

type Tokens = ReadonlyMap<string, string>;
type LinearRgb = readonly [number, number, number];

const css = readFileSync(path.join(__dirname, 'theme.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

function declarations(selector: string): Map<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`theme.css has no ${selector} block`);
  const body = css.slice(start, css.indexOf('}', start));
  const tokens = new Map<string, string>();
  for (const [, name, value] of body.matchAll(/(--[\w-]+):\s*([^;]+);/g)) {
    if (name && value) tokens.set(name, value.trim());
  }
  return tokens;
}

const light = declarations('@theme');
const schemes: Record<'light' | 'dark', Tokens> = {
  light,
  dark: new Map([...light, ...declarations('.dark')]),
};

function resolve(tokens: Tokens, name: string): string {
  const value = tokens.get(name);
  if (value === undefined) throw new Error(`${name} is not defined`);
  const ref = /^var\((--[\w-]+)\)$/.exec(value)?.[1];
  return ref ? resolve(tokens, ref) : value;
}

// OKLab -> linear sRGB, Ottosson's reference matrices.
function oklchToLinearSrgb(value: string): LinearRgb {
  const match = /^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)\s*\)$/.exec(value);
  if (!match) throw new Error(`not an oklch() colour: ${value}`);
  const [, l, percent, c, h] = match;
  const L = Number(l) / (percent ? 100 : 1);
  const a = Number(c) * Math.cos((Number(h) * Math.PI) / 180);
  const b = Number(c) * Math.sin((Number(h) * Math.PI) / 180);
  const l3 = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m3 = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s3 = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
    -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
    -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3,
  ];
}

function luminance(value: string): number {
  const [r, g, b] = oklchToLinearSrgb(value);
  const clip = (v: number) => Math.min(Math.max(v, 0), 1);
  return 0.2126 * clip(r) + 0.7152 * clip(g) + 0.0722 * clip(b);
}

function contrast(fg: string, bg: string): number {
  const [a, b] = [luminance(fg), luminance(bg)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

const TEXT = 4.5;
const GRAPHIC = 3;
// Utility names, as in `text-muted` / `bg-side`; resolved through `--color-<name>`.
const pairs: [fg: string, bg: string, floor: number][] = [
  ...['ink', 'muted', 'accent-ink', 'soft'].flatMap((fg) =>
    ['ground', 'side', 'hair', 'raised', 'selected'].map((bg): [string, string, number] => [
      fg,
      bg,
      TEXT,
    ]),
  ),
  ['accent-ink', 'accent-tint', TEXT],
  ['accent', 'ground', GRAPHIC],
  ['accent', 'side', GRAPHIC],
  ['on-accent', 'accent', TEXT],
  ['on-inverse', 'inverse', TEXT],
  ['inverse-soft', 'inverse', TEXT],
  ...['positive', 'warning', 'negative'].flatMap((fg): [string, string, number][] => [
    [fg, 'ground', TEXT],
    [fg, `${fg}-tint`, TEXT],
  ]),
  // The Impostazioni row of a vendor with errors: text on its tint, then the switch track
  // (off: line-strong; on while errors remain: warning).
  ...['ink', 'soft', 'muted'].map((fg): [string, string, number] => [fg, 'negative-tint', TEXT]),
  ['line-strong', 'negative-tint', GRAPHIC],
  ['warning', 'negative-tint', GRAPHIC],
  ['on-lime', 'lime', TEXT],
  ['on-negative', 'negative', TEXT],
  ['neutral-dot', 'ground', GRAPHIC],
  ['neutral-dot', 'raised', GRAPHIC],
  ['line-strong', 'ground', GRAPHIC],
  ...['chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5'].flatMap((fg) =>
    ['ground', 'raised'].map((bg): [string, string, number] => [fg, bg, GRAPHIC]),
  ),
];

const colour = (tokens: Tokens, name: string) => resolve(tokens, `--color-${name}`);

describe('theme.css', () => {
  it.each(
    Object.entries(schemes).flatMap(([scheme, tokens]) =>
      pairs.map(([fg, bg, floor]) => ({ scheme, tokens, fg, bg, floor })),
    ),
  )('$scheme: $fg on $bg reaches $floor:1', ({ tokens, fg, bg, floor }) => {
    expect(contrast(colour(tokens, fg), colour(tokens, bg))).toBeGreaterThanOrEqual(floor);
  });

  // Each display clips an out-of-gamut colour its own way, so the ratios above would not hold.
  it.each(
    Object.entries(schemes).flatMap(([scheme, tokens]) =>
      [...tokens.keys()]
        .filter((name) => resolve(tokens, name).startsWith('oklch('))
        .map((name) => ({ scheme, name, value: resolve(tokens, name) })),
    ),
  )('$scheme: $name is inside sRGB', ({ value }) => {
    for (const channel of oklchToLinearSrgb(value)) {
      expect(channel).toBeGreaterThanOrEqual(-1e-4);
      expect(channel).toBeLessThanOrEqual(1 + 1e-4);
    }
  });
});

// Tailwind drops a utility whose theme variable is missing without a warning, so a removed size
// token would leave the element unsized and nothing would fail.
const SIZE_UTILITIES = [
  'w-thread',
  'w-chat-column',
  'max-w-chat-column',
  'max-w-bubble',
  'w-chat-rail',
  'h-composer',
  'size-avatar-sm',
  'size-avatar-md',
  'size-avatar-lg',
  'h-meter-bar',
  'h-meter-ticks',
  'h-control',
  'h-row',
  'h-topbar',
  'w-sidebar',
];
const TEXT_STYLES = [
  'display',
  'headline',
  'title',
  'feature',
  'lead',
  'body',
  'ui',
  'ui-strong',
  'small',
  'label',
  'metric',
  'kpi',
  'data',
  'data-sm',
  'eyebrow',
];

async function compiledUtilities(candidates: string[]): Promise<string> {
  const require = createRequire(import.meta.url);
  const theme = readFileSync(path.join(__dirname, 'theme.css'), 'utf8').replace(
    '@import "tw-animate-css";',
    '',
  );
  const compiler = await compile(`@import "tailwindcss";\n${theme}`, {
    base: path.join(__dirname, '..'),
    loadStylesheet: async (id) => {
      const file = require.resolve(id === 'tailwindcss' ? 'tailwindcss/index.css' : id);
      return { path: file, base: path.dirname(file), content: readFileSync(file, 'utf8') };
    },
  });
  return compiler.build(candidates);
}

describe('theme.css utilities', () => {
  it('every size utility the app screens use compiles', async () => {
    const out = await compiledUtilities(SIZE_UTILITIES);
    const dropped = SIZE_UTILITIES.filter((name) => !out.includes(`.${name} {`));
    expect(dropped).toEqual([]);
  });

  it('every design text style has a text-* utility with its line height and weight', async () => {
    const candidates = TEXT_STYLES.map((name) => `text-${name}`);
    const out = await compiledUtilities(candidates);
    const dropped = candidates.filter((name) => !out.includes(`.${name} {`));
    expect(dropped).toEqual([]);
    for (const name of TEXT_STYLES) {
      expect(light.has(`--text-${name}--line-height`)).toBe(true);
      expect(light.has(`--text-${name}--font-weight`)).toBe(true);
    }
  });
});
