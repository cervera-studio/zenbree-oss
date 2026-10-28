import { expect, it } from 'vite-plus/test';
import emailNotFound from '@/lib/api/fixtures/email-not-found.json';
import phoneNotFound from '@/lib/api/fixtures/phone-not-found.json';
import { rowSchema } from '@/lib/lists/api';
import { panelColumnSpecs } from '@/lib/lists/column-model';
import { toColumnSpecs } from '@/lib/lists/columns';
import { fixtureRow, lookups, strayStatus } from '@/lib/lists/testing';
import { cardSummary, filterRows } from './list-filter';

const LABELS = (key: string) => (key === 'column.email' ? 'Email' : 'Phone');

const specs = toColumnSpecs([
  { key: 'relevance_score', label: 'Fit', kind: 'score', position: 0 },
  { key: 'name', label: 'Name', kind: 'static', position: 1 },
  { key: 'domain', label: 'Domain', kind: 'static', position: 2 },
  { key: 'city', label: 'City', kind: 'static', position: 3 },
  { key: 'industry', label: 'Industry', kind: 'static', position: 4 },
  { key: 'hospitals', label: 'Sells to hospitals', kind: 'ai', position: 5 },
]);
const row = (id: string, cells: Record<string, unknown>, score: number | null, excluded = false) =>
  rowSchema.parse({ id, seq: 1, cells, relevance_score: score, excluded, lookups: lookups() });
const rows = [
  row('a', { name: 'Acme', domain: 'acme.it', city: 'Milano' }, 82),
  row('b', { name: 'Beta', domain: 'beta.io', city: 'Roma' }, 45),
  row('c', { name: 'Gamma', domain: 'gamma.it', city: 'Milano' }, 90, true),
];
const ids = (list: { id: string }[]) => list.map((r) => r.id);

it('filters loaded rows by chip, then by a case-insensitive match on any visible cell', () => {
  expect(ids(filterRows(rows, specs, '', 'all'))).toEqual(['a', 'b', 'c']);
  // High fit leaves out excluded rows: they never reach a campaign.
  expect(ids(filterRows(rows, specs, '', 'high_fit'))).toEqual(['a']);
  expect(ids(filterRows(rows, specs, '', 'excluded'))).toEqual(['c']);
  expect(ids(filterRows(rows, specs, ' milano ', 'all'))).toEqual(['a', 'c']);
  expect(ids(filterRows(rows, specs, '.IO', 'all'))).toEqual(['b']);
  expect(ids(filterRows(rows, specs, 'milano', 'excluded'))).toEqual(['c']);
  expect(filterRows(rows, specs, 'nowhere', 'all')).toEqual([]);
});

it('titles a card with the first filled cell and two more, skipping score and AI cells', () => {
  expect(
    cardSummary(
      row('x', { domain: 'x.it', city: 'Bari', industry: 'Food', hospitals: 'yes' }, 70),
      specs,
    ),
  ).toEqual({
    title: 'x.it',
    subtitle: 'Bari · Food',
  });
  expect(cardSummary(row('y', {}, null), specs)).toEqual({ title: '—', subtitle: '' });
});

it('never reads the raw phone status on a card or in a search, also when it is the only phone column', () => {
  const panel = panelColumnSpecs(
    [
      { key: 'full_name', label: 'Full name', kind: 'static', position: 0 },
      { key: 'phone_status', label: 'Phone status', kind: 'static', position: 1 },
    ],
    'person',
    LABELS,
  );
  const miss = fixtureRow(phoneNotFound.row);
  expect(cardSummary(miss, panel)).toEqual({ title: 'Ada Rossi', subtitle: '' });
  expect(filterRows([miss], panel, strayStatus('phone_status'), 'all')).toEqual([]);
});

it('never reads the raw email status on a card or in a search, also when it is the only email column', () => {
  const panel = panelColumnSpecs(
    [
      { key: 'first_name', label: 'First name', kind: 'static', position: 0 },
      { key: 'last_name', label: 'Last name', kind: 'static', position: 1 },
      { key: 'email_status', label: 'Email status', kind: 'static', position: 2 },
    ],
    'person',
    LABELS,
  );
  const miss = fixtureRow(emailNotFound);
  expect(cardSummary(miss, panel)).toEqual({ title: 'Carla', subtitle: 'Blu' });
  expect(filterRows([miss], panel, strayStatus('email_status'), 'all')).toEqual([]);
});
