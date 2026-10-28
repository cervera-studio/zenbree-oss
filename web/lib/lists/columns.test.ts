import { describe, expect, it } from 'vite-plus/test';
import { type ListColumn, rowSchema } from './api';
import {
  cellValue,
  displayText,
  type FieldLabel,
  hrefFor,
  isFilling,
  mappingComplete,
  reorderColumns,
  scoreTone,
  toColumnSpecs,
} from './columns';
import { lookups } from './testing';

it('moves columns in both directions without mutating order, ignoring stale or unchanged drops', () => {
  const order = ['name', 'email', 'website'];
  expect(reorderColumns(order, 'name', 'website')).toEqual(['email', 'website', 'name']);
  expect(reorderColumns(order, 'website', 'name')).toEqual(['website', 'name', 'email']);
  expect(reorderColumns(order, 'missing', 'email')).toBe(order);
  expect(reorderColumns(order, 'name', 'missing')).toBe(order);
  expect(reorderColumns(order, 'email', 'email')).toBe(order);
  expect(order).toEqual(['name', 'email', 'website']);
});

const cols: ListColumn[] = [
  { key: 'name', label: 'Name', kind: 'static', position: 1 },
  { key: 'relevance_score', label: 'Fit', kind: 'score', position: 0 },
  { key: 'website', label: 'Website', kind: 'static', position: 2 },
  { key: 'email', label: 'Email', kind: 'static', position: 4 },
  { key: 'employees', label: 'Employees', kind: 'static', position: 3 },
];

describe('toColumnSpecs', () => {
  it('orders by position with score first and picks a renderer per key', () => {
    expect(toColumnSpecs(cols)).toEqual([
      { key: 'relevance_score', label: 'Fit', render: 'score' },
      { key: 'name', label: 'Name', render: 'text' },
      { key: 'website', label: 'Website', render: 'link' },
      { key: 'employees', label: 'Employees', render: 'text' },
      { key: 'email', label: 'Email', render: 'email' },
    ]);
  });

  it('keeps a score column first even when positioned last', () => {
    const specs = toColumnSpecs([
      { key: 'name', label: 'Name', kind: 'static', position: 0 },
      { key: 'relevance_score', label: 'Fit', kind: 'score', position: 9 },
    ]);
    expect(specs.map((s) => s.key)).toEqual(['relevance_score', 'name']);
  });
});

it('translates the English label the API gave a column it named, and keeps every other label', () => {
  const field: FieldLabel = (key) => `t:${key}`;
  const specs = toColumnSpecs(
    [
      { key: 'city', label: 'City', kind: 'static', position: 0 },
      { key: 'linkedin_url', label: 'Linkedin url', kind: 'static', position: 1 },
      { key: 'company_domain', label: 'Sito', kind: 'static', position: 2 },
      { key: 'title', label: 'Title', kind: 'ai', position: 3 },
      { key: 'source', label: 'Source', kind: 'static', position: 4 },
    ],
    field,
  );
  expect(specs.map((s) => s.label)).toEqual([
    't:importFields.city',
    't:importFields.linkedin_url',
    'Sito',
    'Title',
    'Source',
  ]);
});

describe('AI columns', () => {
  const ai = (status?: string): ListColumn => ({
    key: 'ai_hospitals',
    label: 'Hospitals',
    kind: 'ai',
    position: 5,
    config: { prompt: 'Sells to hospitals?', ...(status && { fill: { status } }) },
  });

  it('renders as an AI cell and carries the fill status', () => {
    expect(toColumnSpecs([ai('running')])).toEqual([
      { key: 'ai_hospitals', label: 'Hospitals', render: 'ai', fill: 'running' },
    ]);
  });

  it('keeps polling only while a fill is queued or running', () => {
    expect(isFilling([...cols, ai('queued')])).toBe(true);
    expect(isFilling([...cols, ai('running')])).toBe(true);
    expect(isFilling([...cols, ai('done'), ai('failed'), ai()])).toBe(false);
  });
});

describe('row cells', () => {
  const row = rowSchema.parse({
    id: 'r1',
    seq: 1,
    cells: { name: 'Acme', linkedin_url: 'linkedin.com/company/acme', tags: ['a', 'b'] },
    relevance_score: '82.50',
    excluded: false,
    lookups: lookups(),
  });

  it('parses NUMERIC score strings and keeps snake_case cell keys', () => {
    expect(row.relevance_score).toBe(82.5);
    expect(cellValue(row, 'linkedin_url')).toBe('linkedin.com/company/acme');
    expect(cellValue(row, 'relevance_score')).toBe(82.5);
  });

  it('renders values as text', () => {
    expect(displayText(cellValue(row, 'tags'))).toBe('a, b');
    expect(displayText(undefined)).toBe('');
  });
});

describe('helpers', () => {
  it('adds a scheme to bare links', () => {
    expect(hrefFor('acme.it')).toBe('https://acme.it');
    expect(hrefFor('http://acme.it')).toBe('http://acme.it');
  });

  it('bands scores', () => {
    expect([scoreTone(90), scoreTone(50), scoreTone(10), scoreTone(null)]).toEqual([
      'high',
      'mid',
      'low',
      'none',
    ]);
  });
});

it('requires a name and one of email, domain or LinkedIn before an import', () => {
  expect(mappingComplete('person', ['first_name', '', 'linkedin_url'])).toBe(true);
  expect(mappingComplete('person', ['full_name', 'company_domain'])).toBe(true);
  expect(mappingComplete('person', ['full_name', 'company_name', 'notes'])).toBe(false);
  expect(mappingComplete('person', ['email', 'linkedin_url'])).toBe(false);
  expect(mappingComplete('company', ['name', 'website'])).toBe(true);
  expect(mappingComplete('company', ['name', 'email'])).toBe(false);
});
