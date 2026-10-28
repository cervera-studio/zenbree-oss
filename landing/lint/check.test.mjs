import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { after, describe, it } from 'node:test';

const here = import.meta.dirname;
const probes = JSON.parse(
  readFileSync(path.join(here, '..', '..', 'web', 'lint', 'tailwind-scale.probes.json'), 'utf8'),
);
const root = mkdtempSync(path.join(tmpdir(), 'landing-check-'));
after(() => rmSync(root, { recursive: true, force: true }));

const VALID_THEME = `@import "tailwindcss";
@font-face { font-family: 'X'; src: url(/fonts/x.woff2) format('woff2'); }
@theme { --color-background: #fff; }
@custom-variant phone (@media (max-width: 759px));
@utility bg-sky { background: radial-gradient(red, blue); }
@media (prefers-color-scheme: dark) { :root { --color-background: #000; } }
`;

let n = 0;
// Runs check.mjs on a temp src dir holding `files` ({ 'components/A.astro': text }); returns its exit status and stderr.
function check(files) {
  const dir = path.join(root, `case${n++}`);
  for (const [name, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(dir, name)), { recursive: true });
    writeFileSync(path.join(dir, name), text);
  }
  const run = spawnSync('node', [path.join(here, 'check.mjs'), dir], { encoding: 'utf8' });
  return { ok: run.status === 0, stderr: run.stderr };
}

const astro = (markup) => ({ 'components/A.astro': `---\n---\n${markup}\n` });
const theme = (css) => ({ 'styles/theme.css': css });
// A probe holding a double quote goes in a single-quoted attribute.
const wrap = (probe) =>
  probe.includes('"') ? `<div class='${probe}'></div>` : `<div class="${probe}"></div>`;

describe('arbitrary Tailwind values', () => {
  for (const probe of probes.fail) {
    it(`rejects ${probe}`, () => assert.equal(check(astro(wrap(probe))).ok, false));
  }
  for (const probe of probes.pass) {
    it(`allows ${probe}`, () => assert.equal(check(astro(wrap(probe))).ok, true));
  }
  it('rejects a value in class:list, a frontmatter string and a .ts file', () => {
    assert.equal(check(astro(`<div class:list={['a', { 'w-[46%]': true }]}></div>`)).ok, false);
    assert.equal(check({ 'components/A.astro': `---\nconst c = 'p-[3px]';\n---\n` }).ok, false);
    assert.equal(check({ 'styles/classes.ts': 'export const c = `flex p-[3px]`;\n' }).ok, false);
  });
  it('names the file and line', () => {
    const { stderr } = check(astro('<p></p>\n<div class="p-[3px]"></div>'));
    assert.match(stderr, /A\.astro:4: arbitrary Tailwind value/);
  });
  it('ignores an apostrophe in text and a comment', () => {
    assert.equal(check(astro(`<p>it's fine</p><!-- don't p-[3px] -->`)).ok, true);
  });
});

describe('plain CSS', () => {
  it('rejects a <style> element', () =>
    assert.equal(check(astro('<style>p{color:red}</style>')).ok, false));
  it('rejects a style attribute', () => {
    assert.equal(check(astro('<div\n  class="a"\n  style="margin: 0"></div>')).ok, false);
    assert.equal(check(astro('<div style={`height: 1%`}></div>')).ok, false);
  });
  it('rejects a second .css file', () => {
    assert.equal(check({ ...theme(VALID_THEME), 'styles/extra.css': '' }).ok, false);
  });
  it('rejects a rule in theme.css', () => {
    assert.equal(check(theme(`${VALID_THEME}.a { color: red; }`)).ok, false);
    assert.equal(check(theme(`${VALID_THEME}@layer base { a { color: red; } }`)).ok, false);
  });
  it('rejects anything else in the dark @media', () => {
    const dark = (body) => theme(`@media (prefers-color-scheme: dark) { ${body} }`);
    assert.equal(check(dark('.a { color: red; }')).ok, false);
    assert.equal(check(dark(':root { color: red; }')).ok, false);
    assert.equal(check(theme('@media (max-width: 759px) { :root { --a: 1; } }')).ok, false);
  });
  it('rejects an @utility that is not approved', () => {
    assert.equal(
      check(theme(`${VALID_THEME}@utility scrollbar-none { scrollbar-width: none; }`)).ok,
      false,
    );
  });
  it('accepts the theme stylesheet shape', () => assert.equal(check(theme(VALID_THEME)).ok, true));
});

describe('the real landing/src', () => {
  it('has no findings', () => {
    const run = spawnSync('node', [path.join(here, 'check.mjs')], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stderr);
  });
});
