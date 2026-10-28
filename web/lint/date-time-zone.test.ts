import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vite-plus/test';

const flags = [
  'd.toLocaleDateString(locale, { day: "numeric" })',
  'd.toLocaleTimeString()',
  'd?.toLocaleTimeString()',
  'o.at?.toLocaleDateString(locale)',
  'o.at?.toLocaleDateString?.(locale)',
  'd.toLocaleDateString(locale, { timeZone })',
  'new Intl.DateTimeFormat(locale, { dateStyle: "short" }).format(d)',
  'Intl.DateTimeFormat(locale).format(d)',
  'new Intl.DateTimeFormat().format(d)',
  'Intl.DateTimeFormat().format(d)',
  'new Intl.DateTimeFormat(locale, opts).format(d)',
  'new Intl.DateTimeFormat(locale, { ...opts }).format(d)',
  'wrap(new Intl.DateTimeFormat()).resolvedOptions()',
  'new Intl.DateTimeFormat(locale, { day: "numeric" }).resolvedOptions().locale',
];
const allows = [
  'new Intl.DateTimeFormat(locale, { dateStyle: "short", timeZone }).format(d)',
  'new Intl.DateTimeFormat(locale, { timeZone: "UTC", day: "numeric" }).format(d)',
  'Intl.DateTimeFormat(locale, { "timeZone": timeZone }).format(d)',
  'new Intl.DateTimeFormat(locale, { ...opts, timeZone }).format(d)',
  'Intl.DateTimeFormat().resolvedOptions().timeZone',
  'new Intl.DateTimeFormat().resolvedOptions().timeZone',
  'format.dateTime(d, { dateStyle: "short" })',
];

const preamble = `declare const d: Date;
declare const o: { at?: Date };
declare const locale: string;
declare const timeZone: string;
declare const opts: Intl.DateTimeFormatOptions;
declare const format: { dateTime(d: Date, o?: object): string };
declare function wrap(x: unknown): { resolvedOptions(): object };
`;
const MESSAGE = 'Date formatted in the process zone';

const dir = mkdtempSync(path.join(__dirname, '.probe-'));
afterAll(() => rmSync(dir, { recursive: true, force: true }));
const files = new Map([...flags, ...allows].map((code, n) => [code, path.join(dir, `p${n}.ts`)]));
let oxlint = new Set<string>();
// One process lints every probe, as in tailwind-scale.test.ts.
beforeAll(() => {
  for (const [code, file] of files) writeFileSync(file, `${preamble}export const x = ${code};\n`);
  const { stdout, stderr } = spawnSync(
    'pnpm',
    ['exec', 'oxlint', '-c', path.join(__dirname, '../.oxlintrc.json'), '-f', 'json', dir],
    { encoding: 'utf8' },
  );
  oxlint = new Set(
    (
      JSON.parse(stdout) as {
        diagnostics: { message: string; help?: string; filename: string }[];
      }
    ).diagnostics
      .filter((d) => `${d.message} ${d.help ?? ''}`.includes(MESSAGE))
      .map((d) => path.resolve(d.filename)),
  );
  // Without this, a linter that lints nothing would pass every "allows" probe.
  if (oxlint.size === 0) throw new Error(`oxlint flagged no probe:\n${stdout}${stderr}`);
}, 60_000);

describe('date-time-zone lint', () => {
  const lints = (code: string) => oxlint.has(files.get(code) ?? '');
  it.each(flags)('flags %s', (code) => expect(lints(code)).toBe(true));
  it.each(allows)('allows %s', (code) => expect(lints(code)).toBe(false));
});
