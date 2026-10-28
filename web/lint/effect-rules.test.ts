import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vite-plus/test';

// The effect rules, run through the real .oxlintrc.json: the original
// eslint-plugin-react-you-might-not-need-an-effect, and lint/oxlint for
// no-reset-all-state-on-prop-change. Expectations match what the ESLint plugin reports on the
// same code, except no-reset-all-state-on-prop-change: the plugin counts every read of a state value
// as a useState, so it never flags a component that returns its state. Our rule counts the
// useState calls.

type Case = { name: string; expected: string[]; code: string };

const cases: Case[] = [
  {
    name: 'derived state through reduce, typed useState',
    expected: ['no-derived-state'],
    code: `
      export function Cart({ items }: { items: { price: number }[] }) {
        const [total, setTotal] = useState<number>(0);
        useEffect(() => {
          setTotal(items.reduce((s, i) => s + i.price, 0));
        }, [items]);
        return total;
      }`,
  },
  {
    name: 'derived state, React namespace hooks',
    expected: ['no-derived-state'],
    code: `
      export function Search({ query }: { query: string }) {
        const [normalized, setNormalized] = React.useState('');
        React.useEffect(() => {
          setNormalized(query.trim().toLowerCase());
        }, [query]);
        return normalized;
      }`,
  },
  {
    name: 'derived state, expression-bodied effect',
    expected: ['no-derived-state'],
    code: `
      export function FullName({ first, last }: { first: string; last: string }) {
        const [full, setFull] = useState('');
        useEffect(() => setFull(first + ' ' + last), [first, last]);
        return full;
      }`,
  },
  {
    name: 'state reset on prop change',
    expected: ['no-adjust-state-on-prop-change'],
    code: `
      export function Filters({ category }: { category: string }) {
        const [page, setPage] = useState(1);
        const [sort, setSort] = useState('asc');
        useEffect(() => {
          setPage(1);
        }, [category]);
        return [page, sort, setSort];
      }`,
  },
  {
    name: 'every piece of state reset on prop change',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export function Profile({ userId }: { userId: string }) {
        const [comment, setComment] = useState('');
        const [rating, setRating] = useState(0);
        useEffect(() => {
          setComment('');
          setRating(0);
        }, [userId]);
        return [comment, rating];
      }`,
  },
  {
    name: 'every piece of state reset, rendered as JSX, arrow component',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export const Note = ({ id }: { id: string }) => {
        const [text, setText] = React.useState<string>('');
        useEffect(() => {
          setText('');
        }, [id]);
        return <p>{text}</p>;
      };`,
  },
  {
    name: 'one piece of state kept on prop change',
    expected: ['no-adjust-state-on-prop-change'],
    code: `
      export function Search({ scope }: { scope: string }) {
        const [query, setQuery] = useState('');
        const [open, setOpen] = useState(false);
        useEffect(() => {
          setQuery('');
        }, [scope]);
        return [query, open, setOpen];
      }`,
  },
  {
    name: 'state set to a value other than its initial one',
    expected: ['no-derived-state'],
    code: `
      export function Pager({ total }: { total: number }) {
        const [page, setPage] = useState(1);
        useEffect(() => {
          setPage(total);
        }, [total]);
        return page;
      }`,
  },
  {
    name: 'a custom hook that resets its state',
    expected: ['no-adjust-state-on-prop-change'],
    code: `
      export function useSelection(listId: string) {
        const [picked, setPicked] = useState<string[]>([]);
        useEffect(() => {
          setPicked([]);
        }, [listId]);
        return picked;
      }`,
  },
  {
    name: 'a custom hook written as an arrow function that resets its state',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export const useQuery = (listId: string) => {
        const [query, setQuery] = useState('');
        useEffect(() => {
          setQuery('');
        }, [listId]);
        return query;
      };`,
  },
  {
    name: 'a component nested in a `const useX =` hook that resets its state',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export const useWidget = () => {
        function Widget({ id }: { id: string }) {
          const [text, setText] = useState('');
          useEffect(() => {
            setText('');
          }, [id]);
          return text;
        }
        return Widget;
      };`,
  },
  {
    name: 'a generator hook that resets its state, which the hook-name test misses',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export function* useQuery(id: string) {
        const [text, setText] = useState('');
        useEffect(() => {
          setText('');
        }, [id]);
        yield text;
      }`,
  },
  {
    name: 'every piece of state reset, effect with an expression body',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export function Notes({ id }: { id: string }) {
        const [note, setNote] = useState('');
        useEffect(() => setNote(''), [id]);
        return note;
      }`,
  },
  {
    name: 'every piece of state reset, initial value on several lines',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export function Filters({ id }: { id: string }) {
        const [filters, setFilters] = useState({
          open: false,
        });
        useEffect(() => {
          setFilters({ open: false });
        }, [id]);
        return filters.open;
      }`,
  },
  {
    name: 'every piece of state reset in an inner component, on an outer prop',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export function Outer({ id }: { id: string }) {
        function Inner() {
          const [text, setText] = useState('');
          useEffect(() => {
            setText('');
          }, [id]);
          return text;
        }
        return Inner;
      }`,
  },
  {
    name: 'state reset when a field of an outer value changes, not a prop',
    expected: [],
    code: `
      const store = { id: '' };
      export function Row({ id }: { id: string }) {
        const [text, setText] = useState('');
        useEffect(() => {
          setText('');
        }, [store.id]);
        return text + id;
      }`,
  },
  {
    name: 'every piece of state reset in a hook inside a component',
    expected: ['no-adjust-state-on-prop-change', 'no-reset-all-state-on-prop-change'],
    code: `
      export function Panel({ id }: { id: string }) {
        function useDraft() {
          const [draft, setDraft] = useState('');
          useEffect(() => {
            setDraft('');
          }, [id]);
          return draft;
        }
        return useDraft();
      }`,
  },
  {
    name: 'state flag used as an event',
    expected: [
      'no-adjust-state-on-prop-change',
      'no-chain-state-updates',
      'no-event-handler',
      'no-pass-live-state-to-parent',
    ],
    code: `
      export function Form({ onSubmit }: { onSubmit: (v: string) => void }) {
        const [value, setValue] = useState('');
        const [submitted, setSubmitted] = useState(false);
        useEffect(() => {
          if (submitted) {
            onSubmit(value);
            setSubmitted(false);
          }
        }, [submitted, value, onSubmit]);
        return [setValue, setSubmitted];
      }`,
  },
  {
    name: 'state chained from other state',
    expected: ['no-chain-state-updates', 'no-event-handler'],
    code: `
      export function Chain() {
        const [step, setStep] = useState(0);
        const [label, setLabel] = useState('');
        useEffect(() => {
          if (step === 3) setLabel('done');
        }, [step]);
        return [label, setStep];
      }`,
  },
  {
    name: 'state initialized in a mount effect',
    expected: ['no-initialize-state'],
    code: `
      export function Greeting() {
        const [name, setName] = useState('');
        useEffect(() => {
          setName('world');
        }, []);
        return name;
      }`,
  },
  {
    name: 'live state passed to the parent',
    expected: ['no-pass-live-state-to-parent'],
    code: `
      export function Toggle({ onChange }: { onChange: (on: boolean) => void }) {
        const [on, setOn] = useState(false);
        useEffect(() => {
          onChange(on);
        }, [on, onChange]);
        return setOn;
      }`,
  },
  {
    name: 'fetched data passed to the parent',
    expected: ['no-pass-data-to-parent'],
    code: `
      export const Child = ({ onFetched }: { onFetched: (d: unknown) => void }) => {
        const data = useSomeApi();
        useEffect(() => {
          onFetched(data);
        }, [onFetched, data]);
        return null;
      };`,
  },
  {
    name: 'external store subscription',
    expected: ['no-external-store-subscription', 'no-initialize-state'],
    code: `
      export function useOnlineStatus() {
        const [isOnline, setIsOnline] = useState(true);
        useEffect(() => {
          function updateState() {
            setIsOnline(navigator.onLine);
          }
          updateState();
          window.addEventListener('online', updateState);
          return () => window.removeEventListener('online', updateState);
        }, []);
        return isOnline;
      }`,
  },
  {
    name: 'fetch with a stale guard',
    expected: [],
    code: `
      export function Fetcher({ id }: { id: string }) {
        const [data, setData] = useState<string | null>(null);
        useEffect(() => {
          let stale = false;
          fetch('/api/' + id).then((r) => r.text()).then((t) => { if (!stale) setData(t); });
          return () => { stale = true; };
        }, [id]);
        return data;
      }`,
  },
  {
    name: 'interval updating state',
    expected: [],
    code: `
      export function Clock() {
        const [now, setNow] = useState(() => Date.now());
        useEffect(() => {
          const t = setInterval(() => setNow(Date.now()), 1000);
          return () => clearInterval(t);
        }, []);
        return now;
      }`,
  },
  {
    name: 'debounced copy of a prop',
    expected: [],
    code: `
      export function Debounced({ value }: { value: string }) {
        const [debounced, setDebounced] = useState(value);
        useEffect(() => {
          const t = setTimeout(() => setDebounced(value), 300);
          return () => clearTimeout(t);
        }, [value]);
        return debounced;
      }`,
  },
  {
    name: 'a set-prefixed helper that is not a state setter',
    expected: [],
    code: `
      function setTitle(title: string) { document.title = title; }
      export function Title({ title }: { title: string }) {
        const [x] = useState(0);
        useEffect(() => {
          setTitle(title);
        }, [title]);
        return x;
      }`,
  },
  {
    name: 'media query subscription in a custom hook',
    expected: [],
    code: `
      export function useMedia(query: string) {
        const [matches, setMatches] = useState(false);
        useEffect(() => {
          const m = window.matchMedia(query);
          const on = () => setMatches(m.matches);
          m.addEventListener('change', on);
          return () => m.removeEventListener('change', on);
        }, [query]);
        return matches;
      }`,
  },
  {
    name: 'side effect with no state',
    expected: [],
    code: `
      export function Analytics({ page }: { page: string }) {
        useEffect(() => {
          void fetch('/track', { method: 'POST', body: page });
        }, [page]);
        return null;
      }`,
  },
];

const lintDir = __dirname;
const header = "import * as React from 'react';\nimport { useEffect, useState } from 'react';\n";

type OxlintDiagnostic = { code: string; filename: string };

const files = cases.map((c, i) => ({ ...c, file: `case-${i}.tsx` }));
let dir = '';
// file name -> rules that flagged it
const oxlint = new Map<string, Set<string>>();

// Oxlint exits non-zero on a finding; the report is on stdout either way.
function run(args: string[]): string {
  try {
    return execFileSync(path.join(lintDir, '../node_modules/.bin/oxlint'), args, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (e) {
    return (e as { stdout: string }).stdout;
  }
}

beforeAll(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'effect-rules-'));
  for (const { file, code } of files) writeFileSync(path.join(dir, file), header + code);
  const paths = files.map(({ file }) => path.join(dir, file));
  const oxlintOut = run(['-c', path.join(lintDir, '../.oxlintrc.json'), '-f', 'json', ...paths]);
  for (const d of (JSON.parse(oxlintOut) as { diagnostics: OxlintDiagnostic[] }).diagnostics) {
    // The plugin's own no-reset-all-state-on-prop-change is not ours, so it keeps its prefix.
    const rule =
      d.code === 'react-you-might-not-need-an-effect(no-reset-all-state-on-prop-change)'
        ? d.code
        : (/^(?:react-you-might-not-need-an-effect|zenbree)\((.+)\)$/.exec(d.code)?.[1] ??
          `unknown: ${d.code}`);
    const file = path.basename(d.filename);
    oxlint.set(file, (oxlint.get(file) ?? new Set()).add(rule));
  }
}, 30_000);

afterAll(() => rmSync(dir, { recursive: true, force: true }));

describe('you-might-not-need-an-effect in Oxlint', () => {
  it.each(files)('$name', ({ file, expected }) => {
    expect([...(oxlint.get(file) ?? [])].sort()).toEqual(expected);
  });
});
