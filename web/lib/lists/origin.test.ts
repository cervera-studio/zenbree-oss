import { describe, expect, it } from 'vite-plus/test';
import type { ListRow } from '@/lib/lists/api';
import { originGroups } from './origin';

const row = (meta: Record<string, string>) =>
  ({
    cell_meta: Object.fromEntries(
      Object.entries(meta).map(([k, provider]) => [k, { status: 'filled', provider }]),
    ),
  }) as unknown as ListRow;

describe('originGroups', () => {
  it('groups by kind of source and never returns a vendor name', () => {
    const specs = [
      { key: 'email', label: 'Email', render: 'text' as const },
      { key: 'name', label: 'Nome', render: 'text' as const },
      { key: 'address', label: 'Indirizzo', render: 'text' as const },
      { key: 'x', label: 'X', render: 'text' as const },
    ];
    expect(
      originGroups(row({ email: 'apollo', name: 'web', address: 'google_maps', x: 'zzz' }), specs),
    ).toEqual([
      ['database', ['Email']],
      ['web', ['Nome']],
      ['maps', ['Indirizzo']],
      ['other', ['X']],
    ]);
  });
});
