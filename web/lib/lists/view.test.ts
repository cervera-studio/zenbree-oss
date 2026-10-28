import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import emailCatchAll from '@/lib/api/fixtures/email-catch-all.json';
import emailFound from '@/lib/api/fixtures/email-found.json';
import emailNotFound from '@/lib/api/fixtures/email-not-found.json';
import emailNotSearched from '@/lib/api/fixtures/email-not-searched.json';
import emailUnknown from '@/lib/api/fixtures/email-unknown.json';
import type { ListColumn, ListRow } from './api';
import { fixtureRow } from './testing';
import {
  aiColumnCredits,
  companyContacts,
  createdLabel,
  exportRowCounts,
  formatCostPerLead,
  LIST_STATUS,
  listKind,
  missingRequired,
  moreRowsCredits,
  shownTabs,
  tabCounts,
  verifiedEmails,
  visibleRows,
} from './view';

const row = (id: string, over: Partial<ListRow> = {}): ListRow => ({
  id,
  seq: 1,
  cells: {},
  relevance_score: null,
  excluded: false,
  delivered: true,
  lookups: fixtureRow(emailNotSearched).lookups,
  ...over,
});

const verified = fixtureRow(emailFound).lookups;
const unverified = fixtureRow(emailUnknown).lookups;

describe('LIST_STATUS', () => {
  it('maps every status to its label key and pill tone', () => {
    expect(LIST_STATUS).toEqual({
      ready: { labelKey: 'status.ready', tone: 'positive' },
      running: { labelKey: 'status.running', tone: 'accent' },
      needs_input: { labelKey: 'status.needs_input', tone: 'warning' },
      empty: { labelKey: 'status.empty', tone: 'warning' },
      plan_pending: { labelKey: 'status.plan_pending', tone: 'accent' },
    });
  });
});

describe('listKind', () => {
  it('shows an enrich-mode list as Arricchimento whatever its entity', () => {
    expect(listKind({ mode: 'enrich', entity: 'person' })).toBe('enrich');
    expect(listKind({ mode: 'build', entity: 'person' })).toBe('person');
    expect(listKind({ mode: 'build', entity: 'company' })).toBe('company');
  });
});

describe('formatCostPerLead', () => {
  it('divides credits by delivered rows with one decimal in the locale', () => {
    expect(formatCostPerLead(298, 47, 'it')).toBe('6,3');
    expect(formatCostPerLead(298, 47, 'en')).toBe('6.3');
    expect(formatCostPerLead(50, 31, 'it')).toBe('1,6');
  });

  it('says «<0,1» when the cost rounds to zero', () => {
    expect(formatCostPerLead(1, 47, 'it')).toBe('<0,1');
    expect(formatCostPerLead(1, 47, 'en')).toBe('<0.1');
  });

  it('is a dash when a figure is missing or nothing was delivered', () => {
    expect(formatCostPerLead(undefined, 47, 'it')).toBe('—');
    expect(formatCostPerLead(298, undefined, 'it')).toBe('—');
    expect(formatCostPerLead(298, 0, 'it')).toBe('—');
    expect(formatCostPerLead(null, null, 'it')).toBe('—');
    expect(formatCostPerLead(0, 47, 'it')).toBe('—');
  });
});

describe('createdLabel', () => {
  const now = new Date(2026, 9, 3, 12, 0);
  it('tells today, yesterday and older days apart', () => {
    expect(createdLabel(new Date(2026, 9, 3, 11, 42).toISOString(), now).kind).toBe('today');
    expect(createdLabel(new Date(2026, 9, 3, 0, 5).toISOString(), now).kind).toBe('today');
    expect(createdLabel(new Date(2026, 9, 2, 23, 59).toISOString(), now).kind).toBe('yesterday');
    const older = createdLabel(new Date(2026, 8, 29, 9, 0).toISOString(), now);
    expect(older.kind).toBe('date');
  });
});

describe('missingRequired (#531)', () => {
  const col = (key: string, label: string, required: boolean): ListColumn => ({
    key,
    label,
    kind: 'static',
    position: 0,
    config: required ? { required: true } : {},
  });
  const columns: ListColumn[] = [
    col('full_name', 'Nome', true),
    col('email', 'Email', true),
    col('phone', 'Telefono', true),
    col('title', 'Ruolo', false),
  ];

  const lookups = (phone: ListRow['lookups']['phone']): ListRow['lookups'] => ({
    ...fixtureRow(emailNotSearched).lookups,
    phone,
  });
  const asked = lookups({ state: 'not_searched' });
  const withStatuses = [
    ...columns,
    col('email_status', 'Stato email', true),
    col('phone_status', 'Stato telefono', true),
  ];

  it('names the required cells a row misses, other than its email', () => {
    const miss = (cells: ListRow['cells']) => missingRequired({ cells, lookups: asked }, columns);
    expect(miss({ full_name: 'Lia Neri' })).toEqual(['Telefono']);
    expect(miss({})).toEqual(['Nome', 'Telefono']);
    expect(miss({ full_name: 'Lia Neri', phone: '+39' })).toEqual([]);
  });

  it('names a required phone status while no phone search stored one, as the API gap rule does', () => {
    const cells = { full_name: 'Lia Neri', phone: '+39' };
    for (const state of ['not_searched', 'refunded'] as const) {
      expect(missingRequired({ cells, lookups: lookups({ state }) }, withStatuses)).toEqual([
        'Stato telefono',
      ]);
    }
  });

  it('skips a required phone status once a search stored one', () => {
    const cells = { full_name: 'Lia Neri', phone: '+39' };
    for (const phone of [
      { state: 'found' },
      { state: 'not_found' },
      { state: 'failed' },
    ] as const) {
      expect(missingRequired({ cells, lookups: lookups(phone) }, withStatuses)).toEqual([]);
    }
  });

  it('skips a required email status: the draft asks for the email search that writes it', () => {
    const cells = { full_name: 'Lia Neri', phone: '+39' };
    const only = [...columns, col('email_status', 'Stato email', true)];
    expect(missingRequired({ cells, lookups: lookups({ state: 'found' }) }, only)).toEqual([]);
  });

  it('skips a required cell cleared by hand, as the API does', () => {
    const row = {
      cells: { full_name: 'Lia Neri' },
      cell_meta: { phone: { status: 'manual' } },
      lookups: asked,
    };
    expect(missingRequired(row, columns)).toEqual([]);
  });
});

describe('tabs and search', () => {
  const rows = [
    row('a', { cells: { full_name: 'Giulia Ferri', company_domain: 'officinanove.it' } }),
    row('b', { cells: { full_name: 'Paolo Marchi', email: 'p@retesei.it' }, delivered: false }),
    row('c', { cells: { full_name: 'Sara Villa' }, excluded: true, delivered: false }),
    row('d', { cells: { name: 'Nodo Agency', domain: 'nodoagency.com' } }),
  ];

  it('counts each tab; an excluded row is neither delivered nor pending', () => {
    expect(tabCounts(rows)).toEqual({
      all: 4,
      delivered: 2,
      pending: 1,
      no_contact: 0,
      excluded: 1,
    });
  });

  it('filters by tab', () => {
    expect(visibleRows(rows, 'delivered', '').map((r) => r.id)).toEqual(['a', 'd']);
    expect(visibleRows(rows, 'pending', '').map((r) => r.id)).toEqual(['b']);
    expect(visibleRows(rows, 'excluded', '').map((r) => r.id)).toEqual(['c']);
  });

  it('searches name, domain and email case-insensitively inside the tab', () => {
    expect(visibleRows(rows, 'all', ' FERRI ').map((r) => r.id)).toEqual(['a']);
    expect(visibleRows(rows, 'all', 'nodoagency').map((r) => r.id)).toEqual(['d']);
    expect(visibleRows(rows, 'all', 'retesei.it').map((r) => r.id)).toEqual(['b']);
    expect(visibleRows(rows, 'delivered', 'retesei')).toEqual([]);
  });

  it('keeps at a fit floor only rows scored at or over it, and no unscored row', () => {
    const scored = [
      row('a', { relevance_score: 79 }),
      row('b', { relevance_score: 80 }),
      row('c', { relevance_score: null }),
      row('d', { relevance_score: 96.5, excluded: true }),
    ];
    expect(visibleRows(scored, 'all', '', 80).map((r) => r.id)).toEqual(['b', 'd']);
    expect(visibleRows(scored, 'excluded', '', 80).map((r) => r.id)).toEqual(['d']);
    expect(visibleRows(scored, 'all', '', 50).map((r) => r.id)).toEqual(['a', 'b', 'd']);
    expect(visibleRows(scored, 'all', '').map((r) => r.id)).toEqual(['a', 'b', 'c', 'd']);
  });

  it('drops rows as the fit floor rises, and keeps only scored rows at or over a floor', () => {
    const score = fc.option(fc.double({ min: 0, max: 100, noNaN: true }), { nil: null });
    const floor = fc.integer({ min: 0, max: 100 });
    fc.assert(
      fc.property(fc.array(score), floor, floor, (scores, x, y) => {
        const rows = scores.map((s, i) => row(String(i), { relevance_score: s }));
        const [low, high] = x <= y ? [x, y] : [y, x];
        const loose = visibleRows(rows, 'all', '', low);
        const strict = visibleRows(rows, 'all', '', high);
        expect(strict.every((r) => loose.includes(r))).toBe(true);
        const kept = loose.map((r) => r.relevance_score);
        expect(kept.every((s) => s !== null && s >= low)).toBe(true);
      }),
    );
  });
});

describe('exportRowCounts', () => {
  it('counts delivered, high-fit and with-email rows among the delivered ones', () => {
    const rows = [
      row('a', { relevance_score: 92, cells: { email: 'a@x.it' } }),
      row('b', { relevance_score: 70, cells: {} }),
      row('c', { relevance_score: 69, cells: { email: 'c@x.it' } }),
      row('d', {
        relevance_score: 95,
        cells: { email: 'd@x.it' },
        excluded: true,
        delivered: false,
      }),
      row('e', { relevance_score: 90, cells: { email: 'e@x.it' }, delivered: false }),
    ];
    expect(exportRowCounts(rows)).toEqual({ all: 3, high_fit: 2, with_email: 2 });
  });

  it('drops unverified and off-target rows when asked', () => {
    const rows = [
      row('a', { relevance_score: 92, cells: { email: 'a@x.it' }, lookups: verified }),
      row('b', { relevance_score: 90, cells: { email: 'b@x.it' }, lookups: unverified }),
      row('c', {
        relevance_score: 80,
        rating: 'bad',
        cells: { email: 'c@x.it' },
        lookups: verified,
      }),
    ];
    const none = { verified_only: false, hide_off_target: false };
    expect(exportRowCounts(rows, { ...none, verified_only: true })).toMatchObject({ all: 2 });
    expect(exportRowCounts(rows, { ...none, hide_off_target: true })).toMatchObject({ all: 2 });
    expect(exportRowCounts(rows, { verified_only: true, hide_off_target: true })).toEqual({
      all: 1,
      high_fit: 1,
      with_email: 1,
    });
  });
});

describe('verifiedEmails', () => {
  it('counts verified emails on the rows not excluded', () => {
    const rows = [
      row('a', { lookups: verified }),
      row('b', { lookups: verified, delivered: false }),
      row('c', { lookups: verified, excluded: true }),
      row('d', { lookups: fixtureRow(emailCatchAll).lookups }),
      row('e', { lookups: fixtureRow(emailNotFound).lookups }),
    ];
    expect(verifiedEmails(rows)).toBe(2);
  });
});

describe('company contacts', () => {
  const companies = [
    row('a', { people_count: 2 }),
    row('b', { people_count: 0 }),
    row('c', { people_count: 0, excluded: true }),
    row('d', { people_count: 1, delivered: false }),
  ];

  it('counts the companies with at least one person, excluded ones aside', () => {
    expect(companyContacts(companies)).toEqual({ done: 2, total: 3 });
  });

  it('has no figure before the chat has a Persone list', () => {
    expect(companyContacts([row('a'), row('b')])).toBeNull();
  });

  it('files the companies without a person under «Senza contatto», shown only when it holds one', () => {
    const counts = tabCounts(companies);
    expect(counts.no_contact).toBe(1);
    expect(visibleRows(companies, 'no_contact', '').map((r) => r.id)).toEqual(['b']);
    expect(shownTabs(counts)).toEqual(['all', 'delivered', 'pending', 'no_contact', 'excluded']);
    expect(shownTabs(tabCounts([row('a')]))).toEqual(['all', 'delivered', 'pending']);
  });
});

const PRICES = { search: 0, email: 1, phone: 8, ai_cell: 1, ai_cells_per_run: 200 };

describe('moreRowsCredits', () => {
  const col = (key: string, kind: ListColumn['kind'] = 'static'): ListColumn => ({
    key,
    label: key,
    kind,
    position: 0,
  });

  it('prices the search, then each paid column the new rows will fill', () => {
    expect(moreRowsCredits(PRICES, [col('name'), col('domain')], 50)).toBe(0);
    expect(moreRowsCredits(PRICES, [col('full_name'), col('email')], 25)).toBe(25);
    expect(moreRowsCredits(PRICES, [col('email'), col('phone'), col('sdr', 'ai')], 100)).toBe(1000);
    expect(moreRowsCredits({ ...PRICES, search: 2 }, [col('name')], 10)).toBe(20);
  });
});

describe('aiColumnCredits', () => {
  it('is a credit per delivered row, up to what one fill run researches', () => {
    expect(aiColumnCredits(PRICES, 47)).toBe(47);
    expect(aiColumnCredits(PRICES, 0)).toBe(0);
    expect(aiColumnCredits(PRICES, 1200)).toBe(200);
    expect(aiColumnCredits({ ...PRICES, ai_cell: 3, ai_cells_per_run: 10 }, 47)).toBe(30);
  });
});
