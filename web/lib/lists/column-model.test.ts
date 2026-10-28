import { describe, expect, it } from 'vite-plus/test';
import type { ListColumn, ListRow } from './api';
import { buildColumnModel, identity, panelColumnSpecs } from './column-model';

const col = (key: string, position: number, over: Partial<ListColumn> = {}): ListColumn => ({
  key,
  label: key,
  kind: 'static',
  position,
  ...over,
});

const PERSON: ListColumn[] = [
  col('relevance_score', 0, { kind: 'score' }),
  col('full_name', 1, { config: { required: true } }),
  col('title', 2),
  col('company_name', 3, { config: { required: true } }),
  col('company_domain', 4),
  col('email', 5, { config: { required: true } }),
  col('email_status', 6),
  col('employees', 8),
  col('ai_why', 9, { kind: 'ai' }),
  col('city', 10),
];

describe('buildColumnModel', () => {
  it('folds the person keys into Fit, Persona, Azienda and Email and keeps the rest in order', () => {
    const { composites, rest } = buildColumnModel(PERSON, 'person');
    expect(composites.map((c) => c.id)).toEqual(['fit', 'person', 'company', 'email']);
    expect(rest.map((s) => s.key)).toEqual(['employees', 'ai_why', 'city']);
  });

  it('folds the phone and its raw status into one Telefono column', () => {
    const { composites, rest } = buildColumnModel(
      [...PERSON, col('phone', 11), col('phone_status', 12)],
      'person',
    );
    expect(composites.map((c) => c.id)).toEqual(['fit', 'person', 'company', 'email', 'phone']);
    expect(rest.map((s) => s.key)).toEqual(['employees', 'ai_why', 'city']);
  });

  it('stars a composite when one of its consumed columns is required', () => {
    const { composites } = buildColumnModel(PERSON, 'person');
    expect(composites.map((c) => [c.id, c.required])).toEqual([
      ['fit', false],
      ['person', true],
      ['company', true],
      ['email', true],
    ]);
  });

  it('sorts the rest by position, not by array order', () => {
    const { rest } = buildColumnModel([col('b', 9), col('a', 3)], 'person');
    expect(rest.map((s) => s.key)).toEqual(['a', 'b']);
  });

  it('drops a composite none of whose columns exist, and shows no Persona on a company list', () => {
    const company = [
      col('relevance_score', 0, { kind: 'score' }),
      col('name', 1),
      col('domain', 2),
      col('industry', 3),
    ];
    const model = buildColumnModel(company, 'company');
    expect(model.composites.map((c) => c.id)).toEqual(['fit', 'company']);
    expect(model.rest.map((s) => s.key)).toEqual(['industry']);
    const csv = buildColumnModel([col('full_name', 1), col('email', 2)], 'person');
    expect(csv.composites.map((c) => c.id)).toEqual(['person', 'email']);
  });
});

describe('identity', () => {
  const r = (cells: ListRow['cells']): ListRow => ({
    id: 'r',
    seq: 1,
    cells,
    relevance_score: null,
    excluded: false,
    lookups: { email: { state: 'not_searched' }, phone: { state: 'not_searched' } },
  });

  it('reads a person, falling back to first and last name', () => {
    expect(
      identity(
        r({
          full_name: 'Giulia Ferri',
          title: 'CEO',
          company_name: 'Officina Nove',
          company_domain: 'officinanove.it',
        }),
        'person',
      ),
    ).toEqual({
      name: 'Giulia Ferri',
      role: 'CEO',
      company: 'Officina Nove',
      domain: 'officinanove.it',
    });
    expect(identity(r({ first_name: 'Luca', last_name: 'Conti' }), 'person').name).toBe(
      'Luca Conti',
    );
  });

  it('reads a company as its own name, with the website when there is no domain', () => {
    expect(
      identity(r({ name: 'Nodo Agency', website: 'https://nodoagency.com' }), 'company'),
    ).toEqual({
      name: 'Nodo Agency',
      role: '',
      company: 'Nodo Agency',
      domain: 'https://nodoagency.com',
    });
  });
});

describe('panelColumnSpecs labels', () => {
  const labels = (key: string) => (key === 'column.email' ? 'Email' : 'Telefono');
  const col = (key: string, label: string, position: number): ListColumn => ({
    key,
    label,
    kind: 'static',
    position,
  });

  it('labels a status column that stands alone like its value column', () => {
    const specs = panelColumnSpecs(
      [col('phone_status', 'Stato telefono', 0), col('email_status', 'Stato email', 1)],
      'person',
      labels,
    );
    expect(specs.map((s) => [s.key, s.label])).toEqual([
      ['phone', 'Telefono'],
      ['email', 'Email'],
    ]);
  });

  it('labels the folded cell the same before and after a row finds the value', () => {
    const base = [col('full_name', 'Full name', 0), col('phone_status', 'Phone status', 1)];
    const phoneLabel = (cols: ListColumn[]) =>
      panelColumnSpecs(cols, 'person', labels).find((s) => s.key === 'phone')?.label;
    expect(phoneLabel(base)).toBe('Telefono');
    expect(phoneLabel([...base, col('phone', 'Phone', 2)])).toBe('Telefono');
  });

  it('keeps the value column and drops its status', () => {
    const specs = panelColumnSpecs(
      [col('phone', 'Phone', 0), col('phone_status', 'Stato telefono', 1)],
      'person',
      labels,
    );
    expect(specs.map((s) => [s.key, s.label])).toEqual([['phone', 'Telefono']]);
  });

  it('heads each contact column with the label key of the list page', () => {
    const contact = [
      col('email_status', 'Stato email', 0),
      col('phone', 'Phone', 1),
      col('phone_status', 'Stato telefono', 2),
    ];
    const page = buildColumnModel(contact, 'person').composites.map((c) => [c.id, c.labelKey]);
    const panel = panelColumnSpecs(contact, 'person', (key) => key).map((s) => [s.key, s.label]);
    expect(panel).toEqual(page);
  });

  it('translates the English labels of the cells it does not fold', () => {
    const specs = panelColumnSpecs(
      [col('full_name', 'Full name', 0), col('company_name', 'Company name', 1)],
      'person',
      (key) => key,
    );
    expect(specs.map((s) => s.label)).toEqual([
      'importFields.full_name',
      'importFields.company_name',
    ]);
  });
});
