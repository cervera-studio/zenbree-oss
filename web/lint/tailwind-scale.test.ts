import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vite-plus/test';
import probes from './tailwind-scale.probes.json';

const dir = mkdtempSync(path.join(__dirname, '.probe-'));
afterAll(() => rmSync(dir, { recursive: true, force: true }));

const files = new Map(
  [...probes.fail, ...probes.pass].map((cls, n) => [cls, path.join(dir, `p${n}.tsx`)]),
);
const indexing = path.join(dir, 'indexing.ts');
// The other nodes that hold a class string, and two that only look like one. `VALUE` becomes an
// arbitrary value when the file is written, so this file holds none.
const VALUE = ['p', '[3px]'].join('-');
const shapes = {
  fail: {
    'a JSX attribute': 'export const a = <i className="VALUE" />;',
    'a template chunk': 'export const a = `flex VALUE`;',
    'an object key': "export const a = { 'VALUE': true };",
  },
  pass: {
    'an import path': "export * from 'VALUE';",
    'a string type': "export type A = 'VALUE';",
  },
};
const shapeFiles = new Map(
  [...Object.entries(shapes.fail), ...Object.entries(shapes.pass)].map(([name, code], n) => [
    name,
    { code, file: path.join(dir, `s${n}.tsx`) },
  ]),
);
let oxlint = new Set<string>();
// One process lints every probe: a process per probe took 1-2 s and timed out on a loaded machine.
beforeAll(() => {
  for (const [cls, file] of files)
    writeFileSync(file, `export const x = ${JSON.stringify(cls)};\n`);
  writeFileSync(
    indexing,
    'declare const m: string[]; declare let i: number; declare function f(n: unknown): void;\n' +
      'export const a = m[i];\nexport const b = f(--i);\n',
  );
  for (const { code, file } of shapeFiles.values())
    writeFileSync(file, `${code.replace('VALUE', VALUE)}\n`);
  const paths = [...files.values(), indexing, ...[...shapeFiles.values()].map((s) => s.file)];
  const config = path.join(__dirname, '../.oxlintrc.json');
  const o = spawnSync('pnpm', ['exec', 'oxlint', '-c', config, '-f', 'json', ...paths], {
    encoding: 'utf8',
  });
  oxlint = new Set(
    (JSON.parse(o.stdout) as { diagnostics: { code: string; filename: string }[] }).diagnostics
      .filter((d) => d.code === 'zenbree(tailwind-scale)')
      .map((d) => path.resolve(d.filename)),
  );
  // Without this, a linter that lints nothing would pass every "allows" probe.
  if (oxlint.size === 0) throw new Error(`oxlint flagged no probe:\n${o.stdout}${o.stderr}`);
}, 60_000);

it('keeps tailwind-scale.patterns.json in step with its generator', () => {
  execFileSync('node', ['lint/gen-tailwind-scale.mjs', '--check']);
});

describe('tailwind-scale lint', () => {
  const lints = (cls: string) => !oxlint.has(files.get(cls) ?? '');
  it.each(probes.fail)('rejects %s', (cls) => expect(lints(cls)).toBe(false));
  it.each(probes.pass)('allows %s', (cls) => expect(lints(cls)).toBe(true));
  it('allows indexing and call syntax that only resembles a utility', () => {
    expect(oxlint.has(indexing)).toBe(false);
  });
  const shape = (name: string) => oxlint.has(shapeFiles.get(name)?.file ?? '');
  it.each(Object.keys(shapes.fail))('rejects a value in %s', (name) =>
    expect(shape(name)).toBe(true),
  );
  it.each(Object.keys(shapes.pass))('allows %s', (name) => expect(shape(name)).toBe(false));
});
