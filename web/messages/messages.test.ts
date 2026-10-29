import { type MessageFormatElement, parse, TYPE } from '@formatjs/icu-messageformat-parser';
import { describe, expect, it } from 'vite-plus/test';
import { LOCALES } from '../i18n/config';
import { MESSAGES } from './index';

function keys(obj: object, prefix = ''): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === 'object' ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`],
  );
}

function entries(obj: object, prefix = ''): [string, string][] {
  return Object.entries(obj).flatMap(([k, v]): [string, string][] =>
    typeof v === 'string' ? [[`${prefix}${k}`, v]] : entries(v, `${prefix}${k}.`),
  );
}

/** Each argument of a message: `true` when it is a plain `{x}` (a string), `false` when it is formatted. */
function argKinds(nodes: MessageFormatElement[], kinds = new Map<string, boolean>()) {
  for (const node of nodes) {
    if (node.type === TYPE.tag) argKinds(node.children, kinds);
    else if (node.type === TYPE.plural || node.type === TYPE.select) {
      kinds.set(node.value, false);
      for (const option of Object.values(node.options)) argKinds(option.value, kinds);
    } else if (node.type !== TYPE.literal && node.type !== TYPE.pound) {
      kinds.set(node.value, kinds.get(node.value) ?? node.type === TYPE.argument);
    }
  }
  return kinds;
}

// English is the base language: every other one is checked against it.
describe.each(LOCALES.filter((locale) => locale !== 'en'))('%s messages', (locale) => {
  it('have the same keys as en', () => {
    const [base, own] = [new Set(keys(MESSAGES.en)), new Set(keys(MESSAGES[locale]))];
    expect(
      {
        missing: [...base].filter((key) => !own.has(key)).sort(),
        extra: [...own].filter((key) => !base.has(key)).sort(),
      },
      `keys of messages/*.${locale}.json against *.en.json`,
    ).toEqual({ missing: [], extra: [] });
  });

  // `pnpm typecheck` checks the arguments against `en` only. A message that takes a plain `{n}`
  // where `en` formats a number would show the count unformatted and unpluralised.
  it('take the same arguments as plain strings as en', () => {
    const own = new Map(entries(MESSAGES[locale]));
    const differing = entries(MESSAGES.en).flatMap(([key, text]) => {
      const other = argKinds(parse(own.get(key) ?? ''));
      return [...argKinds(parse(text))]
        .filter(([name, plain]) => other.has(name) && other.get(name) !== plain)
        .map(([name]) => `${key}: {${name}}`);
    });
    expect(differing, `format the argument the same way in en and ${locale}`).toEqual([]);
  });

  // The call site is typed by `en`, so it cannot pass an argument that only this language names.
  it('name no argument that en lacks', () => {
    const base = new Map(entries(MESSAGES.en));
    const unknown = entries(MESSAGES[locale]).flatMap(([key, text]) => {
      const known = argKinds(parse(base.get(key) ?? ''));
      return [...argKinds(parse(text)).keys()]
        .filter((name) => !known.has(name))
        .map((name) => `${key}: {${name}}`);
    });
    expect(unknown, 'give the en message the argument too, in a select or plural').toEqual([]);
  });
});
