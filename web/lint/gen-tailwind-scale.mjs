// Generates tailwind-scale.patterns.json, the regexes of the tailwind-scale rule, read by
// lint/oxlint/tailwind-scale.mjs and landing/lint/check.mjs. They come from the Grit version of the
// rule, whose regexes had no look-ahead, so "an arbitrary value that is NOT one of the allowed forms"
// is spelled as the complement of a prefix trie.
// Run: node lint/gen-tailwind-scale.mjs. With --check it only compares (tailwind-scale.test.ts runs it).
import { readFileSync, writeFileSync } from 'node:fs';

const NOT_VALUE = String.raw`[^\]\s]`;
const esc = (s) => s.replace(/[()\\.[\]*+?|{}^$]/g, '\\$&');

function buildTrie(words) {
  const root = { kids: new Map(), end: false };
  for (const w of words) {
    let n = root;
    for (const c of w) {
      if (!n.kids.has(c)) n.kids.set(c, { kids: new Map(), end: false });
      n = n.kids.get(c);
    }
    n.end = true;
  }
  return root;
}

function* walk(node, prefix = '') {
  yield [prefix, node];
  for (const [c, kid] of node.kids) yield* walk(kid, prefix + c);
}

// Value (after `[`, up to and including `]`) that does not start with one of `prefixes`.
function valueNotStartingWith(prefixes) {
  const alts = [];
  for (const [p, node] of walk(buildTrie(prefixes))) {
    if (node.end) continue;
    const kids = [...node.kids.keys()].map(esc).join('');
    const extra = p === '' ? String.raw`\-\.0-9` : '';
    if (p !== '') alts.push(`${esc(p)}\\]`);
    alts.push(`${esc(p)}[^\\]\\s${kids}${extra}]${NOT_VALUE}*\\]`);
  }
  return alts;
}

// Value that is a number whose unit is not one of `units`.
function numberNotWithUnit(units) {
  const alts = [];
  for (const [p, node] of walk(buildTrie(units))) {
    if (node.end) {
      alts.push(`${esc(p)}${NOT_VALUE}+\\]`);
      continue;
    }
    const kids = [...node.kids.keys()].map(esc).join('');
    const extra = p === '' ? '0-9.' : '';
    alts.push(`${esc(p)}\\]`);
    alts.push(`${esc(p)}[^\\]\\s${kids}${extra}]${NOT_VALUE}*\\]`);
  }
  return `-?[0-9.]*[0-9](?:${alts.join('|')})`;
}

// Utility name (before `-[`) that is not one of `names`.
function utilityNotNamed(names) {
  const alts = [];
  for (const [p, node] of walk(buildTrie(names))) {
    if (node.end) {
      alts.push(`${esc(p)}[a-z0-9-]+`);
      continue;
    }
    if (p !== '') alts.push(esc(p));
    const alphabet = [
      ...(p === '' ? 'abcdefghijklmnopqrstuvwxyz' : 'abcdefghijklmnopqrstuvwxyz0123456789-'),
    ];
    const rest = alphabet.filter((c) => !node.kids.has(c));
    alts.push(`${esc(p)}[${rest.join('').replace('-', '\\-')}][a-z0-9-]*`);
  }
  return alts;
}

const BOUND = String.raw`(?:^|[\s\"'\x60:!])`;
// An opacity modifier may follow (`bg-x/50`); a `/name` before `:` is a named-group variant.
const TERM = String.raw`(?:/[^\s:\"'\x60!]*)?(?:[\s\"'\x60!]|$)`;

const valueViolation = [
  ...valueNotStartingWith(['calc(', 'clamp(', 'min(', 'max(', 'env(']),
  String.raw`-[^0-9.\]\s]${NOT_VALUE}*\]`,
  numberNotWithUnit(['dvh', 'svh', 'lvh', 'vh', 'vw']),
].join('|');

const utility = utilityNotNamed(['grid-cols', 'grid-rows', 'transition']).join('|');
const colour = String.raw`(?:#[0-9a-fA-F]{3}|(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb|color-mix|color)\(|var\(--color|color:)`;

const patterns = [
  // utility-[value]
  `${BOUND}-?(?:${utility})-\\[(?:${valueViolation})${TERM}`,
  // [property:value]
  `${BOUND}\\[[a-z-]+:(?:${valueViolation})${TERM}`,
  // a colour inside any arbitrary value, even an allowed form
  `${BOUND}-?[a-z][a-z0-9-]*-\\[${NOT_VALUE}*${colour}${NOT_VALUE}*\\]${TERM}`,
  // utility-(--variable)
  `${BOUND}-?[a-z][a-z0-9-]*-\\((?:[a-z-]+:)?--[^)\\s]*\\)${TERM}`,
  // min-[760px]: and max-[759px]: breakpoint variants
  `${BOUND}(?:min|max)-\\[${NOT_VALUE}*\\]:`,
];

const jsPatterns = `${JSON.stringify(patterns, null, 2)}\n`;
const jsOutput = new URL('./tailwind-scale.patterns.json', import.meta.url);

if (process.argv.includes('--check')) {
  if (readFileSync(jsOutput, 'utf8') !== jsPatterns) {
    console.error('tailwind-scale.patterns.json is stale: run node lint/gen-tailwind-scale.mjs');
    process.exit(1);
  }
} else {
  writeFileSync(jsOutput, jsPatterns);
}
