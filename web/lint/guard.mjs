// Runs in `pnpm lint`. Keeps the lint rules in this folder from being switched off quietly:
// - biome.json matches BIOME_JSON_SHA256 and is the only Biome config under web/ (any config edit,
//   `extends` or nested config can disable a rule, such as `noConsole`);
// - .oxlintrc.json matches OXLINT_JSON_SHA256, is the only Oxlint config under web/, and turns on
//   every rule of lint/oxlint: its `jsPlugins` and `rules` load the rules, so an edit or a nested
//   config can drop one;
// - no ignore file hides a source file from Oxlint: no `.eslintignore` in or above web/, and no
//   tracked source file matches a .gitignore rule (Oxlint reads both; Biome reads neither);
// - the `lint` script in package.json is LINT_SCRIPT, so no flag turns a rule off;
// - every `oxlint-disable` or `eslint-disable` comment is in APPROVED, since Oxlint runs only these
//   rules, and so is a blanket `biome-ignore lint` (`-all` or `-start` too), which turns off every
//   Biome rule at once.
//
// Agents: never edit APPROVED, the hashes, LINT_SCRIPT or these checks yourself. Fix the code
// (web/lint/README.md). If you believe a hit is a false alarm, or a config needs a change, stop
// and ask the user. Only the user approves it, and the PR records that approval.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import zenbree from './oxlint/index.mjs';

/** `<file relative to web/>: <suppressed category or rules>`, one line per comment. */
const APPROVED = [
  // syncs a server-rendered prop into the external chat store (#203)
  'components/chat/chat-app.tsx: oxlint-disable-next-line react-you-might-not-need-an-effect/no-event-handler',
  // takes `?draft=` out of the URL after a navigation from another page (#899)
  'components/chat/chat-window.tsx: oxlint-disable-next-line react-you-might-not-need-an-effect/no-event-handler',
];
/** sha256 of biome.json, parsed and re-serialised. After an approved change: `node lint/guard.mjs --hash`. */
const BIOME_JSON_SHA256 = '0271cb9373e2fda2b919efd3fb94400c03700faaa6e780c09d32114aaaf14382';
/** sha256 of .oxlintrc.json, the same way. */
const OXLINT_JSON_SHA256 = '555d640d58ca0ea6e434b45838f13d0953e572490ec5678ecc19a51b549de27c';
// `-c`: under `vp` (VP_VERSION set), Oxlint without it reads vite.config.ts and runs its defaults.
const LINT_SCRIPT = 'biome check . && oxlint -c .oxlintrc.json && node lint/guard.mjs';

const web = path.join(import.meta.dirname, '..');
const SKIP = new Set(['node_modules', '.next', 'coverage']);
const SOURCE = /\.(?:[cm]?[jt]sx?)$/;
const BIOME_CONFIG = /^\.?biome\.jsonc?$/;
const OXLINT_CONFIG = /^\.?oxlintrc(?:\.jsonc?)?$|^oxlint\.config\.[cm]?[jt]s$/;
// The directive and its rule list, without the `-- reason` and the end of a block comment.
// `biome-ignore lint:` with no group or rule; a suppression of one Biome rule stays allowed.
const BIOME_BLANKET = /biome-ignore(?:-all|-start)?\s+lint(?=[\s:(])/g;
const OXLINT_SUPPRESSION = /\b((?:oxlint|eslint)-disable(?:-next-line|-line)?)\b([^\n]*)/g;

const hashOf = (file) =>
  createHash('sha256')
    .update(JSON.stringify(JSON.parse(readFileSync(path.join(web, file), 'utf8'))))
    .digest('hex');
const hash = hashOf('biome.json');
const oxlintHash = hashOf('.oxlintrc.json');
if (process.argv.includes('--hash')) {
  console.log(`biome.json      ${hash}\n.oxlintrc.json  ${oxlintHash}`);
  process.exit(0);
}

const relative = (file) => path.relative(web, file).split(path.sep).join('/');

function* files(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (SKIP.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* files(full);
    else yield full;
  }
}

const problems = [];

if (hash !== BIOME_JSON_SHA256)
  problems.push(
    "biome.json changed. A config change can switch a rule off, so it needs the user's approval;\nthen update BIOME_JSON_SHA256 in web/lint/guard.mjs (`node lint/guard.mjs --hash`).",
  );
if (oxlintHash !== OXLINT_JSON_SHA256)
  problems.push(
    ".oxlintrc.json changed. A config change can switch the effect rules off, so it needs the user's approval;\nthen update OXLINT_JSON_SHA256 in web/lint/guard.mjs (`node lint/guard.mjs --hash`).",
  );
const script = JSON.parse(readFileSync(path.join(web, 'package.json'), 'utf8')).scripts?.lint;
if (script !== LINT_SCRIPT)
  problems.push(
    `package.json: the lint script must be \`${LINT_SCRIPT}\`; a change needs the user's approval.`,
  );
const oxlint = JSON.parse(readFileSync(path.join(web, '.oxlintrc.json'), 'utf8'));
const off = Object.keys(zenbree.rules).filter((r) => oxlint.rules?.[`zenbree/${r}`] !== 'error');
if (!oxlint.jsPlugins?.includes('./lint/oxlint/index.mjs') || off.length > 0)
  problems.push(
    `.oxlintrc.json must load ./lint/oxlint/index.mjs and set every rule in it to "error"; not on: ${off.join(', ')}`,
  );

const ignored = execFileSync('git', ['ls-files', '-ci', '--exclude-standard', '--', '.'], {
  cwd: web,
  encoding: 'utf8',
})
  .split('\n')
  .filter((f) => SOURCE.test(f));
if (ignored.length > 0)
  problems.push(
    `Oxlint skips these tracked files, because a .gitignore rule matches them; remove the rule:\n${ignored.map((f) => `  ${f}`).join('\n')}`,
  );

// Oxlint also reads a .eslintignore above web/, up to the repo root and beyond.
for (let dir = path.dirname(web); ; dir = path.dirname(dir)) {
  if (existsSync(path.join(dir, '.eslintignore')))
    problems.push(
      `${path.join(dir, '.eslintignore')}: Oxlint skips the files it names. Remove it.`,
    );
  if (dir === path.dirname(dir)) break;
}

const found = [];
for (const file of files(web)) {
  const name = path.basename(file);
  if (BIOME_CONFIG.test(name) && relative(file) !== 'biome.json')
    problems.push(`${relative(file)}: web/biome.json must be the only Biome config.`);
  if (name === '.eslintignore')
    problems.push(`${relative(file)}: Oxlint skips the files it names. Remove it.`);
  if (OXLINT_CONFIG.test(name) && relative(file) !== '.oxlintrc.json')
    problems.push(`${relative(file)}: web/.oxlintrc.json must be the only Oxlint config.`);
  if (!SOURCE.test(name) || file === import.meta.filename) continue;
  const text = readFileSync(file, 'utf8');
  for (const match of text.matchAll(BIOME_BLANKET)) found.push(`${relative(file)}: ${match[0]}`);
  for (const [, directive, rest] of text.matchAll(OXLINT_SUPPRESSION)) {
    const ruleList = rest
      .split('--')[0]
      .replace(/\*\/.*$/, '')
      .trim();
    found.push(`${relative(file)}: ${[directive, ruleList].filter(Boolean).join(' ')}`);
  }
}

const unapproved = [...found];
for (const entry of APPROVED) {
  const i = unapproved.indexOf(entry);
  if (i !== -1) unapproved.splice(i, 1);
}
if (unapproved.length > 0)
  problems.push(
    [
      'Unapproved suppression of the lint rules:',
      ...unapproved.map((s) => `  ${s}`),
      'Fix the code instead: see web/lint/README.md and https://react.dev/learn/you-might-not-need-an-effect.',
      'Moving the code into a helper so the rule goes quiet is not a fix either.',
      'If you believe this is a false alarm, stop and ask the user. Only the user adds entries to',
      'APPROVED in web/lint/guard.mjs.',
    ].join('\n'),
  );
const stale = APPROVED.filter((entry) => !found.includes(entry));
if (stale.length > 0)
  problems.push(
    [
      'Approved suppressions no longer in the code; remove them from APPROVED:',
      ...stale.map((s) => `  ${s}`),
    ].join('\n'),
  );

if (problems.length > 0) {
  console.error(problems.join('\n\n'));
  process.exit(1);
}
