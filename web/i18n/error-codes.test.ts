import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vite-plus/test';
import { MESSAGES } from '@/messages';
import { LOCALES } from './config';

// The user reads an error by its code (`useErrorText`), so every code the API raises needs a text in
// both languages, or it falls back to the generic one. Admin codes reach only the back office, which
// shows them raw on purpose.

const web = path.join(import.meta.dirname, '..');
const CODE = String.raw`'([A-Z][A-Z_]*\.[A-Z][A-Z_0-9]*)'`;
// `'code': 'X.Y'`, `err('X.Y'`, a call that takes a status then a code (`_error(404, 'X.Y'`) and a
// status map (`{404: 'X.Y'}`). Codes built at runtime (`f'REQUEST.HTTP_{status}'`) are not seen.
const API_CODE = new RegExp(
  String.raw`(?:'code':\s*|\berr\(\s*|\(\s*\d{3},\s*|\b\d{3}:\s*)${CODE}`,
  'g',
);
const WEB_CODE = new RegExp(String.raw`new AppError\(\s*${CODE}`, 'g');

function codesIn(dir: string, ext: RegExp, pattern: RegExp): Set<string> {
  const codes = new Set<string>();
  for (const file of readdirSync(dir, { recursive: true, encoding: 'utf8' })) {
    if (!ext.test(file) || file.includes('node_modules')) continue;
    for (const [, code] of readFileSync(path.join(dir, file), 'utf8').matchAll(pattern)) {
      if (code && !code.startsWith('ADMIN.')) codes.add(code);
    }
  }
  return codes;
}

// An error's `message` is the API's English for logs. The back office shows it on purpose; a form
// field's issues are this app's own; a ban carries its reason and expiry in it (`decodeBan`).
const READS_MESSAGE = /\b(?:error|err|e)\??\.message\b/;
const MAY_READ_MESSAGE = ['components/ui/field.tsx', 'app/auth/sign-in/sign-in-form.tsx'];

function readsMessage(dir: string): string[] {
  return readdirSync(path.join(web, dir), { recursive: true, encoding: 'utf8' })
    .map((file) => path.join(dir, file))
    .filter(
      (file) =>
        file.endsWith('.tsx') &&
        !file.endsWith('.test.tsx') &&
        !file.includes('admin/') &&
        !MAY_READ_MESSAGE.includes(file) &&
        READS_MESSAGE.test(readFileSync(path.join(web, file), 'utf8')),
    );
}

function has(messages: object, code: string): boolean {
  const found = code
    .split('.')
    .reduce<unknown>(
      (node, key) =>
        node && typeof node === 'object' ? (node as Record<string, unknown>)[key] : undefined,
      (messages as { errors: unknown }).errors,
    );
  return typeof found === 'string';
}

const apiCodes = codesIn(path.join(web, '..', 'api', 'app'), /\.py$/, API_CODE);
const webCodes = new Set(
  ['app', 'lib', 'components'].flatMap((dir) => [
    ...codesIn(path.join(web, dir), /\.tsx?$/, WEB_CODE),
  ]),
);

describe('error codes', () => {
  it('finds the codes it checks', () => {
    expect(apiCodes.size).toBeGreaterThan(100);
    expect(webCodes).toContain('AUTH.TOKEN_MISSING');
  });

  it.each(LOCALES)('every code a user can hit has an %s text', (locale) => {
    const missing = [...apiCodes, ...webCodes].filter((code) => !has(MESSAGES[locale], code));
    expect(missing.sort(), `add errors.<CODE> to messages/errors.${locale}.json`).toEqual([]);
  });

  it("no screen shows an error's message: it shows useErrorText()", () => {
    expect(['app', 'components'].flatMap(readsMessage)).toEqual([]);
  });
});
