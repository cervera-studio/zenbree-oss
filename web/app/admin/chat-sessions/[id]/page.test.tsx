import { createTranslator } from 'next-intl';
import type { ReactNode } from 'react';
import { isValidElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { FORMATS } from '@/i18n/config';
import summaryAllFailed from '@/lib/api/fixtures/summary-email-all-failed.json';
import summaryNoRowMatch from '@/lib/api/fixtures/summary-email-no-row-match.json';
import summaryNoSource from '@/lib/api/fixtures/summary-email-no-source.json';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { MESSAGES } from '@/messages';

const SESSION = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';
const call = (id: string, summary: unknown, verdict: 'success' | 'failed' = 'success') => ({
  id,
  name: 'enrich_email',
  input: {},
  result: '{}',
  creditsUsed: 0,
  verdict,
  summary: toCamelCase(summary),
});

vi.mock('@/lib/admin/chats', () => ({
  getChatSession: async () => ({
    success: true,
    data: {
      id: SESSION,
      userId: null,
      email: null,
      name: null,
      title: 'Email batch',
      createdAt: '2026-10-13T10:00:00Z',
      updatedAt: '2026-10-13T10:00:00Z',
      messages: [
        {
          id: 'm1',
          role: 'assistant',
          content: 'done',
          question: null,
          creditsUsed: 0,
          createdAt: '2026-10-13T10:00:00Z',
          toolCalls: [
            call('c1', summaryAllFailed),
            call('c2', summaryNoSource),
            call('c3', summaryNoRowMatch, 'failed'),
          ],
        },
      ],
      summary: {
        jobId: null,
        creditsSpent: 40,
        creditsReturned: 10,
        llmCostUsd: 0,
        vendorCostUsd: 0,
        companies: 0,
        people: 0,
        eurPerUsd: 1,
        eurPer1000Credits: null,
        revenueEur: null,
        marginEur: null,
      },
    },
  }),
}));
vi.mock('@/components/admin/user-actions', () => ({ ImpersonateButton: () => null }));
vi.mock('next-intl/server', () => ({
  getTranslations: async (namespace: string) =>
    createTranslator({
      locale: 'it',
      messages: MESSAGES.it,
      namespace: namespace as 'admin',
      formats: FORMATS,
    }),
  getFormatter: async () => ({ dateTime: () => '10:00:00', number: (n: number) => String(n) }),
  getLocale: async () => 'it',
  getTimeZone: async () => 'Europe/Rome',
}));

import ChatTranscript from './page';

/** Awaits the async server components of a tree, so that it renders statically. */
async function settle(node: ReactNode): Promise<ReactNode> {
  if (Array.isArray(node)) return Promise.all(node.map(settle));
  if (!isValidElement<{ children?: ReactNode }>(node)) return node;
  const { type, props } = node;
  if (typeof type === 'function' && type.constructor.name === 'AsyncFunction') {
    return settle(await (type as (p: unknown) => Promise<ReactNode>)(props));
  }
  return { ...node, props: { ...props, children: await settle(props.children) } };
}

describe('the admin transcript', () => {
  it('draws each tool call with the state the chat chip draws for it (#548 #549)', async () => {
    const page = await ChatTranscript({
      params: Promise.resolve({ id: SESSION }),
      searchParams: Promise.resolve({ open: '1' }),
    });
    const html = renderToStaticMarkup(<TestIntl>{await settle(page)}</TestIntl>);
    const lines = html.split('<details').slice(1);
    expect(lines).toHaveLength(3);
    expect(lines[0]).toContain('aria-label="Da riprovare"');
    expect(lines[0]).toContain('2 ricerche non riuscite');
    expect(lines[0]).not.toContain('lucide-check');
    expect(lines[1]).toContain('aria-label="Da riprovare"');
    expect(lines[1]).toContain('nessuna fonte attiva');
    expect(lines[2]).toContain('aria-label="Errore"');
    expect(lines[2]).toContain('Nessuna riga corrisponde');
    expect(html).toContain('50 spesi · 10 resi');
    expect(html).toContain('Netto');
    expect(html).toMatch(/Netto<\/dt>.*?>40</);
  });

  it('marks the active view link with aria-current="true", not aria-pressed (#823)', async () => {
    const page = await ChatTranscript({
      params: Promise.resolve({ id: SESSION }),
      searchParams: Promise.resolve({ open: '1' }),
    });
    const html = renderToStaticMarkup(<TestIntl>{await settle(page)}</TestIntl>);
    const links =
      html.match(/<a [^>]*>/g)?.filter((a) => a.includes('/admin/chat-sessions/')) ?? [];
    const on = links.filter((a) => a.includes('aria-current="true"'));
    expect(links).toHaveLength(2);
    expect(on).toHaveLength(1);
    expect(on[0]).not.toContain('?');
    expect(html).not.toContain('aria-pressed');
  });
});
