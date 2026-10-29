import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { type MessageFormatElement, parse, TYPE } from '@formatjs/icu-messageformat-parser';
import { describe, expect, it } from 'vite-plus/test';

// «1 righe», «1 crediti»: a count followed by its noun must agree with one. Reviews caught it twice
// (c32fad65, 2666da23) while 77 more shipped, so a message that puts a word after a number
// argument puts both inside a plural of the same argument, and that plural has a branch for 1.

const messages = path.join(import.meta.dirname, '..', 'messages');

// Words that can follow any count unchanged: «3 di 10», «2 da leggere», «4 in campagna», «5 unread»,
// «3 de 10», «2 sin leer», «3 sur 10», «4 dans la campagne».
const AGREES_WITH_ANY = new Set([
  'di',
  'da',
  'in',
  'su',
  'e',
  'de',
  'en',
  'sin',
  'sur',
  'dans',
  'au',
  'of',
  'to',
  'and',
  'out',
  'unread',
  'delivered',
  'approved',
  'skipped',
  'reviewed',
  'required',
  'cr',
  'banned',
  'interested',
  // A currency code after a rate: «0,92 EUR/USD».
  'eur',
]);

// A number after one of these labels names an item, not a quantity: «Step 3 sent», «v2 approved»,
// «Paso 3 enviado», «Étape 3 envoyée».
const NUMBERS_ITEM = new Set(['step', 'v', 'paso', 'étape']);

function* strings(value: unknown, key: string): Generator<[string, string]> {
  if (typeof value === 'string') yield [key, value];
  else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) yield* strings(v, key ? `${key}.${k}` : k);
  }
}

const parsed = readdirSync(messages)
  .filter((f) => f.endsWith('.json'))
  .flatMap((file) =>
    [...strings(JSON.parse(readFileSync(path.join(messages, file), 'utf8')), '')].map(
      ([key, text]) => [`${file} ${key}`, parse(text)] as const,
    ),
  );

const hits = (find: (nodes: MessageFormatElement[]) => Iterable<string>) =>
  parsed.flatMap(([where, nodes]) => [...find(nodes)].map((hit) => `${where}: ${hit}`));

// «1 crediti non usati scaduti» came from a plural with only =0 and other branches.
const hasOne = (options: Record<string, unknown>) => 'one' in options || '=1' in options;

function unagreedCount(nodes: MessageFormatElement[], i: number, plurals: Set<string>) {
  const [before, node, next] = [nodes[i - 1], nodes[i], nodes[i + 1]];
  if (node?.type !== TYPE.number || plurals.has(node.value)) return undefined;
  const label = before?.type === TYPE.literal ? /(\p{L}+)\s*$/u.exec(before.value)?.[1] : undefined;
  if (label && NUMBERS_ITEM.has(label.toLowerCase())) return undefined;
  const word = next?.type === TYPE.literal ? /^\s+(\p{L}+)/u.exec(next.value)?.[1] : undefined;
  return word && !AGREES_WITH_ANY.has(word.toLowerCase()) ? `{${node.value}} ${word}` : undefined;
}

function* unagreed(nodes: MessageFormatElement[], plurals: Set<string>): Generator<string> {
  for (const [i, node] of nodes.entries()) {
    const hit = unagreedCount(nodes, i, plurals);
    if (hit) yield hit;
    if (node.type === TYPE.plural || node.type === TYPE.select) {
      const inner =
        node.type === TYPE.plural && hasOne(node.options)
          ? new Set([...plurals, node.value])
          : plurals;
      for (const option of Object.values(node.options)) yield* unagreed(option.value, inner);
    }
    if (node.type === TYPE.tag) yield* unagreed(node.children, plurals);
  }
}

// `#` formats with the locale defaults, which leave Italian 4-digit counts ungrouped («1234»).
// Only a branch that matches 1 or an exact value can print it.
function* ungroupedPounds(nodes: MessageFormatElement[], branch?: string): Generator<string> {
  for (const node of nodes) {
    if (node.type === TYPE.pound && branch) yield branch;
    if (node.type === TYPE.plural) {
      for (const [key, option] of Object.entries(node.options)) {
        const exact = key === 'one' || key.startsWith('=');
        yield* ungroupedPounds(option.value, exact ? undefined : `{${node.value}} ${key} {#}`);
      }
    }
    if (node.type === TYPE.select) {
      for (const option of Object.values(node.options))
        yield* ungroupedPounds(option.value, branch);
    }
    if (node.type === TYPE.tag) yield* ungroupedPounds(node.children, branch);
  }
}

describe('counts', () => {
  it('a number followed by its noun sits in a plural of the same argument with a one branch', () => {
    expect(
      hits((nodes) => unagreed(nodes, new Set())),
      'write {n, plural, one {# row} other {{n, number, credits} rows}}, in every language',
    ).toEqual([]);
  });

  it('a plural prints counts above one through a named number format', () => {
    expect(
      hits((nodes) => ungroupedPounds(nodes)),
      'write other {{n, number, credits} righe}, not other {# righe}: # leaves «1234» ungrouped in Italian',
    ).toEqual([]);
  });
});
