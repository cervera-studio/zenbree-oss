import { describe, expect, it } from 'vite-plus/test';
import type { Schema } from '@/lib/api/schema';
import { type Action, initialState, reducer } from './csv-import-state';

type Preview = Schema<'CsvPreview'>;
type Imp = Schema<'CsvImportState'>;

const preview = (mapping: string[]) => ({ mapping, incomplete: 2, to_import: 5 }) as Preview;
const imp = (status: string) => ({ id: 'i1', status, cursor: null }) as unknown as Imp;
const file = new File(['a'], 'report.csv');

const run = (...actions: Action[]) => actions.reduce(reducer, initialState);
const loaded = (): Action[] => [
  { type: 'entityChosen', entity: 'company' },
  { type: 'previewLoaded', file, preview: preview(['name', 'domain']) },
];

describe('csv import reducer', () => {
  it('previewLoaded goes to map with the name, mapping and find-emails default', () => {
    expect(run(...loaded())).toMatchObject({
      step: 'map',
      entity: 'company',
      file,
      name: 'report',
      mapping: ['name', 'domain'],
      findEmails: true,
    });
  });

  it('requestStarted sets busy and clears the error, requestFailed the reverse', () => {
    const notice = { title: 'x', body: 'y' };
    const failed = run({ type: 'requestFailed', notice });
    expect(failed).toMatchObject({ busy: false, error: notice });
    expect(reducer(failed, { type: 'requestStarted' })).toMatchObject({ busy: true, error: null });
    expect(reducer(failed, { type: 'requestStarted' })).not.toBe(failed);
  });

  it('a back move clears the error', () => {
    const notice = { title: 'x', body: 'y' };
    const state = run(...loaded(), { type: 'requestFailed', notice });
    expect(reducer(state, { type: 'stepChanged', to: 'file' })).toMatchObject({
      step: 'file',
      error: null,
    });
  });

  it('map -> file keeps only the entity', () => {
    const state = reducer(run(...loaded()), { type: 'stepChanged', to: 'file' });
    expect(state).toEqual({ step: 'file', entity: 'company', busy: false, error: null });
  });

  it('review -> map keeps name, mapping and find-emails for the way forward', () => {
    const next = preview(['name', 'domain']);
    const state = run(
      ...loaded(),
      { type: 'mappingChanged', mapping: ['name', ''] },
      { type: 'reviewLoaded', preview: next },
      { type: 'nameChanged', name: 'Edited' },
      { type: 'findEmailsToggled', value: false },
      { type: 'stepChanged', to: 'map' },
      { type: 'stepChanged', to: 'review' },
    );
    expect(state).toMatchObject({
      step: 'review',
      name: 'Edited',
      findEmails: false,
      mapping: ['name', ''],
    });
  });

  it('restarted from import keeps the entity and nothing else', () => {
    const state = run(
      ...loaded(),
      { type: 'reviewLoaded', preview: preview([]) },
      { type: 'importStarted', imp: imp('queued'), followUp: 'ask' },
      { type: 'restarted' },
    );
    expect(state).toEqual({ step: 'file', entity: 'company', busy: false, error: null });
  });

  it('importUpdated takes a plain value and an updater in import', () => {
    const started = run(
      ...loaded(),
      { type: 'reviewLoaded', preview: preview([]) },
      { type: 'importStarted', imp: imp('queued'), followUp: 'ask' },
    );
    const plain = reducer(started, { type: 'importUpdated', next: imp('done') });
    expect(plain).toMatchObject({ step: 'import', imp: { status: 'done' }, followUp: 'ask' });
    const updated = reducer(started, {
      type: 'importUpdated',
      next: (i) => (i ? { ...i, status: 'running' } : i),
    });
    expect(updated).toMatchObject({ imp: { status: 'running' } });
  });

  it('importUpdated is ignored outside import and a null result changes nothing', () => {
    const map = run(...loaded());
    expect(reducer(map, { type: 'importUpdated', next: imp('done') })).toBe(map);
    const started = run(
      ...loaded(),
      { type: 'reviewLoaded', preview: preview([]) },
      { type: 'importStarted', imp: imp('queued'), followUp: '' },
    );
    expect(reducer(started, { type: 'importUpdated', next: null })).toBe(started);
  });

  it('importResumed goes from file to import with the same entity', () => {
    const state = run(
      { type: 'entityChosen', entity: 'company' },
      { type: 'importResumed', imp: imp('running') },
    );
    expect(state).toMatchObject({ step: 'import', entity: 'company', followUp: '' });
  });

  it('a stale previewLoaded in import is ignored', () => {
    const resumed = run({ type: 'importResumed', imp: imp('running') });
    expect(reducer(resumed, { type: 'previewLoaded', file, preview: preview([]) })).toBe(resumed);
  });
});
