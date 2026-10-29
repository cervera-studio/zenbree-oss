import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { NavSession } from '@/lib/home/nav';
import { SessionMeta } from './sidebar-chats';

const session = (extra: Partial<NavSession>) =>
  ({
    id: 's',
    status: 'done',
    progressDone: 0,
    progressTotal: 24,
    listCount: 1,
    people: 0,
    companies: 0,
    ...extra,
  }) as NavSession;

const render = (s: NavSession) =>
  renderToStaticMarkup(
    <TestIntl>
      <SessionMeta session={s} metaId="m" />
    </TestIntl>,
  );

describe('SessionMeta', () => {
  it('does not report a found count for a finished chat whose lists are empty (#735)', () => {
    expect(render(session({}))).toContain('Risposta pronta');
  });

  it('reports what a finished chat found', () => {
    expect(render(session({ people: 3, companies: 2 }))).toContain('3 persone');
  });
});
