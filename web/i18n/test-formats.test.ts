import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, it } from 'vite-plus/test';

// A provider, translator or formatter built without `formats` renders {n, number, credits} as «1234»
// in Italian, and next-intl only logs FORMATTING_ERROR. Component tests wrap in `TestIntl`
// (app/auth/testing.tsx), which sets them.

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

// This file is skipped: its own patterns match themselves.
const tests = [...files(web)].filter((f) => /\.test\.tsx?$/.test(f) && f !== import.meta.filename);

function offenders(pattern: RegExp, passes: string) {
  return tests
    .filter((f) => (readFileSync(f, 'utf8').match(pattern) ?? []).some((m) => !m.includes(passes)))
    .map((f) => path.relative(web, f).split(path.sep).join('/'));
}

it('every NextIntlClientProvider a test builds passes the app formats', () => {
  expect(
    offenders(/<NextIntlClientProvider\b[^>]*>/g, 'formats={FORMATS}'),
    'wrap these in <TestIntl> from @/app/auth/testing',
  ).toEqual([]);
});

it('every createTranslator and createFormatter a test calls passes the app formats', () => {
  expect(
    offenders(/\bcreate(?:Translator|Formatter)\([^)]*\)/g, 'formats: FORMATS'),
    'pass `formats: FORMATS` from @/i18n/config to these calls',
  ).toEqual([]);
});
