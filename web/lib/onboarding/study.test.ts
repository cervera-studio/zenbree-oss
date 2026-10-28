import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { type StudyProgress, studyCompany } from './study';

const reply = vi.hoisted(() => ({ response: new Response(null), init: {} as RequestInit }));

vi.mock('@/lib/api/browser', async (actual) => ({
  ...(await actual<typeof import('@/lib/api/browser')>()),
  authFetch: async (_path: string, init: RequestInit) => {
    reply.init = init;
    return reply.response;
  },
}));

function sse(...chunks: string[]) {
  return new Response(
    new ReadableStream({
      start(c) {
        for (const chunk of chunks) c.enqueue(new TextEncoder().encode(chunk));
        c.close();
      },
    }),
    { headers: { 'content-type': 'text/event-stream' } },
  );
}

const PROFILE = {
  company_name: 'Acme',
  summary: 'Anvils',
  personas: ['CEO'],
  markets: [],
  pain_points: ['Slow'],
  solution: '',
  buying_triggers: [],
  exclusions: [],
  proof: '',
};

describe('studyCompany', () => {
  let seen: StudyProgress[];
  beforeEach(() => {
    seen = [];
  });

  it('reports each progress frame, then resolves with the profile', async () => {
    const frame = `event: profile\ndata: ${JSON.stringify({ profile: PROFILE, pages: ['https://acme.it'] })}\n\n`;
    reply.response = sse(
      'event: page\ndata: {"url": "https://acme.it"}\n\nevent: drafting\ndata: {}\n\n',
      'event: offer\ndata: {"summary": "Anvils"}\n\n',
      frame.slice(0, 40),
      frame.slice(40),
    );
    const signal = new AbortController().signal;
    const res = await studyCompany({ website: 'acme.it' }, (p) => seen.push(p), signal);
    expect(reply.init).toMatchObject({ method: 'POST', body: '{"website":"acme.it"}', signal });
    expect(seen).toEqual([
      { type: 'page', url: 'https://acme.it' },
      { type: 'drafting' },
      { type: 'offer', summary: 'Anvils' },
    ]);
    expect(res).toMatchObject({
      success: true,
      data: { profile: { companyName: 'Acme', painPoints: ['Slow'] } },
    });
  });

  it('fails with the code of an error frame', async () => {
    reply.response = sse(
      'event: error\ndata: {"code": "ONBOARDING.SITE_UNREADABLE", "message": "no"}\n\n',
    );
    const res = await studyCompany(
      { website: 'x.it' },
      (p) => seen.push(p),
      new AbortController().signal,
    );
    expect(res).toMatchObject({ success: false, error: { code: 'ONBOARDING.SITE_UNREADABLE' } });
  });

  it('fails with the code of a refusal before the stream', async () => {
    reply.response = Response.json(
      { detail: { code: 'ONBOARDING.LIMIT', message: 'cap' } },
      { status: 429 },
    );
    const res = await studyCompany(
      { description: 'x' },
      (p) => seen.push(p),
      new AbortController().signal,
    );
    expect(res).toMatchObject({ success: false, error: { code: 'ONBOARDING.LIMIT', status: 429 } });
  });

  it('fails when the stream ends with no profile', async () => {
    reply.response = sse('event: drafting\ndata: {}\n\n');
    const res = await studyCompany(
      { description: 'x' },
      (p) => seen.push(p),
      new AbortController().signal,
    );
    expect(res).toMatchObject({ success: false, error: { code: 'INTERNAL.RESPONSE_CONTRACT' } });
  });
});
