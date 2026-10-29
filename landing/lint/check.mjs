// Runs in `pnpm lint`. Keeps the landing's styling in Tailwind classes and its theme tokens:
// - a quoted string in src/**/*.{astro,ts} may not hold an arbitrary Tailwind value (the web's
//   rule: ../web/lint/oxlint/tailwind-scale.mjs, same regexes from tailwind-scale.patterns.json);
// - no <style> element and no style= attribute in an .astro file;
// - no .css file under src except styles/theme.css;
// - theme.css holds only @import, @theme, @font-face, @custom-variant, @utility and the dark
//   @media block of :root custom properties, so every style lives on the element;
// - an @utility must be named in APPROVED_UTILITIES.
//
// Agents: never edit APPROVED_UTILITIES or these checks yourself. Fix the markup (use a scale step,
// a token from theme.css or a stock utility). If you believe a hit is a false alarm, or a new
// @utility is needed, stop and ask the user. Only the user approves it, and the PR records that.
//
// Usage: node lint/check.mjs [srcDir]   (default: ../src)
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

/** `@utility` names the user approved. */
const APPROVED_UTILITIES = [
  // the sky gradient: three uses, and a radial gradient has no stock utility with token stops
  'bg-sky',
];
const THEME_FILE = 'styles/theme.css';
const AT_RULES = new Set(['import', 'theme', 'font-face', 'custom-variant', 'utility', 'media']);
const DARK_MEDIA = '@media (prefers-color-scheme: dark)';

const here = import.meta.dirname;
const src = path.resolve(process.argv[2] ?? path.join(here, '..', 'src'));
const patterns = JSON.parse(
  readFileSync(path.join(here, '..', '..', 'web', 'lint', 'tailwind-scale.patterns.json'), 'utf8'),
).map((p) => new RegExp(p));

const findings = [];
const lineOf = (text, index) => text.slice(0, index).split('\n').length;
const report = (file, text, index, message) =>
  findings.push(`${path.relative(process.cwd(), file)}:${lineOf(text, index)}: ${message}`);

function* files(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* files(full);
    else yield full;
  }
}

// End index (exclusive) of the string that starts at `i`, or -1 when a '/" string hits a newline.
function stringEnd(text, i) {
  const quote = text[i];
  let j = i + 1;
  while (j < text.length) {
    const c = text[j];
    if (c === '\\') j += 2;
    else if (c === quote) return j + 1;
    else if (quote !== '`' && c === '\n') return -1;
    else if (quote === '`' && c === '$' && text[j + 1] === '{') j = expressionEnd(text, j + 2);
    else j++;
  }
  return text.length;
}

// Index just past the `}` that closes a `${` expression.
function expressionEnd(text, i) {
  let depth = 1;
  let j = i;
  while (j < text.length && depth > 0) {
    const c = text[j];
    if (c === '"' || c === "'" || c === '`') {
      const end = stringEnd(text, j);
      j = end === -1 ? j + 1 : end;
      continue;
    }
    if (c === '{') depth++;
    else if (c === '}') depth--;
    j++;
  }
  return j;
}

// Index just past the next `end`, or the end of the text.
function skipTo(text, end, from) {
  const at = text.indexOf(end, from);
  return at === -1 ? text.length : at + end.length;
}

function* strings(text, isTs) {
  let i = 0;
  while (i < text.length) {
    if (text.startsWith('/*', i)) i = skipTo(text, '*/', i + 2);
    else if (text.startsWith('<!--', i)) i = skipTo(text, '-->', i + 4);
    else if (isTs && text.startsWith('//', i)) i = skipTo(text, '\n', i + 2);
    else if (text[i] === '"' || text[i] === "'" || text[i] === '`') {
      const end = stringEnd(text, i);
      if (end === -1) {
        i++;
        continue;
      }
      yield { value: text.slice(i + 1, end - 1), index: i };
      i = end;
    } else i++;
  }
}

const SCALE_MESSAGE =
  'arbitrary Tailwind value. Use a scale step (18px -> 4.5), a token from src/styles/theme.css ' +
  "(write the token's utility, never `[var(--x)]` or `(--x)`), or a stock utility. A viewport " +
  'breakpoint is `phone:` or `tablet:`, never `min-[…]:`/`max-[…]:`. Do not add a ' +
  'token or @utility to silence this: ask the user.';

function checkMarkup(file, text) {
  for (const { value, index } of strings(text, file.endsWith('.ts'))) {
    if (patterns.some((p) => p.test(value))) {
      report(file, text, index, `${SCALE_MESSAGE} In "${value.slice(0, 60)}"`);
    }
  }
  if (!file.endsWith('.astro')) return;
  for (const m of text.matchAll(/<style[\s>]/g)) {
    report(
      file,
      text,
      m.index,
      'a <style> element. Put the style on the element as Tailwind classes.',
    );
  }
  for (const m of text.matchAll(/<[A-Za-z][^>]*?\sstyle\s*=/g)) {
    report(file, text, m.index, 'a style= attribute. Use Tailwind classes, or a theme token.');
  }
}

// Replaces comments with spaces so offsets and lines survive.
const blank = (css) => css.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));

// Index of the `;` or `{` that ends a statement head, skipping parentheses and quotes.
function headEnd(css, from) {
  let paren = 0;
  let quote = '';
  let i = from;
  for (; i < css.length; i++) {
    const c = css[i];
    if (quote) {
      if (c === '\\') i++;
      else if (c === quote) quote = '';
    } else if (c === '"' || c === "'") quote = c;
    else if (c === '(') paren++;
    else if (c === ')') paren--;
    else if (paren === 0 && (c === ';' || c === '{')) break;
  }
  return i;
}

// Index just past the `}` matching the `{` at `open`.
function blockEnd(css, open) {
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth++;
    else if (css[i] === '}' && --depth === 0) return i + 1;
  }
  return css.length;
}

// Splits a CSS body into statements: { head, body | null, index, bodyIndex }.
function statements(css, offset = 0) {
  const out = [];
  let i = 0;
  while (i < css.length) {
    if (/\s/.test(css[i])) {
      i++;
      continue;
    }
    const end = headEnd(css, i);
    const head = css.slice(i, end).trim();
    if (css[end] === '{') {
      const close = blockEnd(css, end);
      const body = css.slice(end + 1, close - 1);
      out.push({ head, body, index: offset + i, bodyIndex: offset + end + 1 });
      i = close;
    } else {
      out.push({ head, body: null, index: offset + i });
      i = end + 1;
    }
  }
  return out;
}

// Problems with one top-level statement of theme.css, as [index, message].
function* themeProblems(s) {
  const name = /^@([a-z-]+)/.exec(s.head)?.[1];
  if (!name || !AT_RULES.has(name)) {
    yield [
      s.index,
      `"${s.head.slice(0, 40)}" in theme.css. Only @import, @theme, @font-face, @custom-variant, @utility and the dark @media block may be here: put the style on the element as a Tailwind class.`,
    ];
  } else if (name === 'media') {
    yield* mediaProblems(s);
  } else if (name === 'utility') {
    const utility = s.head.replace('@utility', '').trim();
    if (!APPROVED_UTILITIES.includes(utility)) {
      yield [
        s.index,
        `@utility ${utility} is not approved. Write the style as Tailwind classes on the element; if you believe a new @utility is needed, stop and ask the user.`,
      ];
    }
  }
}

function* mediaProblems(s) {
  if (s.head.replace(/\s+/g, ' ') !== DARK_MEDIA) {
    yield [
      s.index,
      `"${s.head}" in theme.css. The only @media is ${DARK_MEDIA}; a breakpoint is a @custom-variant.`,
    ];
    return;
  }
  for (const inner of statements(s.body ?? '', s.bodyIndex)) {
    const decls = inner.body === null ? [] : statements(inner.body, inner.bodyIndex);
    if (inner.head !== ':root' || decls.some((d) => !d.head.startsWith('--'))) {
      yield [
        inner.index,
        `"${inner.head}" in the dark @media. Only a :root block of custom properties (--…) may be here.`,
      ];
    }
  }
}

function checkTheme(file, raw) {
  for (const s of statements(blank(raw))) {
    for (const [index, message] of themeProblems(s)) report(file, raw, index, message);
  }
}

for (const file of files(src)) {
  const relative = path.relative(src, file).split(path.sep).join('/');
  if (/\.(?:astro|ts)$/.test(file)) checkMarkup(file, readFileSync(file, 'utf8'));
  else if (file.endsWith('.css')) {
    if (relative === THEME_FILE) checkTheme(file, readFileSync(file, 'utf8'));
    else
      findings.push(
        `${path.relative(process.cwd(), file)}:1: a .css file. The only stylesheet is src/${THEME_FILE}; write styles as Tailwind classes on the element.`,
      );
  }
}

if (findings.length > 0) {
  console.error(findings.join('\n'));
  console.error(`\n${findings.length} landing style finding(s).`);
  process.exit(1);
}
