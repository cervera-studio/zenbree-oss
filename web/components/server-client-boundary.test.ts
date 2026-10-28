import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vite-plus/test';

const root = path.join(import.meta.dirname, '..');
const DIRS = ['app', 'components'];
const CLIENT = /^\s*(['"])use client\1/;
// A file that cannot be client code: it reads the server API, or it is a route's page or layout.
const SERVER =
  /from\s+['"](?:next-intl\/server|server-only|next\/headers)['"]|^import\s+['"]server-only['"]/m;
const IMPORT = /^import\s+(?!type\b)([^;]*?)\s+from\s+['"]([^'"]+)['"]/gms;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return sources(full);
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\./.test(e.name) ? [full] : [];
  });
}

function resolve(from: string, spec: string): string | null {
  const base = spec.startsWith('@/')
    ? path.join(root, spec.slice(2))
    : spec.startsWith('.')
      ? path.resolve(path.dirname(from), spec)
      : null;
  if (!base) return null;
  return (
    ['.tsx', '.ts', '/index.tsx', '/index.ts'].map((x) => base + x).find((f) => exists(f)) ?? null
  );
}

function exists(file: string): boolean {
  try {
    readFileSync(file);
    return true;
  } catch {
    return false;
  }
}

// A server module may render a client component, but a function that is not a component is a client
// reference there: calling it fails at render with "Attempted to call x() from the server" (#932).
describe('server modules and client exports', () => {
  it('a server module (one that reads the server API, a page or a layout) imports no function value from a use client module', () => {
    const all = DIRS.flatMap((d) => sources(path.join(root, d)));
    const clientFiles = new Set(all.filter((f) => CLIENT.test(readFileSync(f, 'utf8'))));
    const offenders: string[] = [];
    for (const file of all) {
      if (clientFiles.has(file)) continue;
      const text = readFileSync(file, 'utf8');
      if (!SERVER.test(text) && !/(?:page|layout)\.tsx$/.test(file)) continue;
      for (const m of text.matchAll(IMPORT)) {
        const [, clause = '', spec = ''] = m;
        const target = resolve(file, spec);
        if (!target || !clientFiles.has(target)) continue;
        const named = [...(clause.match(/\{([^}]*)\}/)?.[1] ?? '').split(',')]
          .map((n) => n.trim())
          .filter((n) => n && !n.startsWith('type '))
          .map((n) => n.split(/\s+as\s+/)[0] ?? n);
        for (const name of named.filter((n) => /^[a-z]/.test(n))) {
          offenders.push(`${path.relative(root, file)} imports ${name} from ${spec}`);
        }
      }
    }
    expect(offenders, 'move the plain function to a module with no use client').toEqual([]);
  });
});
