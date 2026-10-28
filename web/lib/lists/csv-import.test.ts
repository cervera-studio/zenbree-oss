import { describe, expect, it } from 'vite-plus/test';
import type { Schema } from '@/lib/api/schema';
import { applyFrame } from './csv-import';

const imp = { status: 'running', error: null } as Schema<'CsvImportState'>;

describe('applyFrame', () => {
  it('stores the code of a failed frame as the import error', () => {
    const next = applyFrame(imp, { id: '1', event: 'failed', data: '{"code":"LIST.CSV_EMPTY"}' });
    expect(next).toMatchObject({ status: 'failed', error: 'LIST.CSV_EMPTY' });
  });

  it('ends the import with the code of an error frame, so the progress does not stay open', () => {
    const data = '{"message":"gone","code":"AUTH.NOT_A_MEMBER"}';
    const next = applyFrame(imp, { id: '', event: 'error', data });
    expect(next).toMatchObject({ status: 'failed', error: 'AUTH.NOT_A_MEMBER' });
  });
});
