import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const ALPHA = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';
const BETA = '5d2c1a47-9e1b-4c55-b1f0-7f3e2a6c9d20';
const ALPHA_PEOPLE = '1c6a9b52-7d0e-4f3a-9b8c-2e5d4f6a7b81';
const ALPHA_COMPANIES = '2d7b0c63-8e1f-4a4b-8c9d-3f6e5a7b8c92';
const BETA_PEOPLE = '3e8c1d74-9f2a-4b5c-9dae-4a7f6b8c9da3';
const BETA_OLD = '4f9d2e85-a03b-4c6d-8ebf-5b8a7c9daeb4';
const UNKNOWN = '5a0e3f96-b14c-4d7e-9fc0-6c9b8daebfc5';

const LISTS = [
  { id: ALPHA_PEOPLE, name: 'Alpha people', entity: 'person', projectId: ALPHA },
  { id: ALPHA_COMPANIES, name: 'Alpha companies', entity: 'company', projectId: ALPHA },
  { id: BETA_PEOPLE, name: 'Beta people', entity: 'person', projectId: BETA },
  { id: BETA_OLD, name: 'Beta old', entity: 'person', projectId: BETA, pastIndexLimit: true },
];

const state = vi.hoisted(() => ({ active: '' }));

vi.mock('server-only', () => ({}));
vi.mock('@/lib/auth-token', () => ({ getToken: async () => undefined }));
vi.mock('next/headers', () => ({
  cookies: async () => ({
    get: (name: string) => (name === 'zb-project' ? { value: state.active } : undefined),
  }),
}));
vi.mock('@/lib/http-client', () => ({
  httpClient: {
    get: async (path: string) => {
      const url = new URL(path, 'http://api.test');
      if (url.pathname === '/v1/projects') {
        const projects = [
          { id: ALPHA, name: 'Alpha' },
          { id: BETA, name: 'Beta' },
        ];
        return { success: true, data: { projects } };
      }
      if (url.pathname === '/v1/campaigns') return { success: true, data: { campaigns: [] } };
      if (url.pathname === '/v1/lists') {
        const project = url.searchParams.get('project_id');
        const lists = LISTS.filter((l) => !l.pastIndexLimit && l.projectId === project);
        return { success: true, data: { lists } };
      }
      const one = LISTS.find((l) => url.pathname === `/v1/lists/${l.id}`);
      if (one) return { success: true, data: one };
      if (url.pathname.startsWith('/v1/lists/')) {
        return { success: false, error: { code: 'LIST.NOT_FOUND', message: '' } };
      }
      throw new Error(`unexpected GET ${path}`);
    },
  },
}));
vi.mock('next-intl/server', () => ({ getTranslations: async () => (key: string) => key }));
vi.mock('@/lib/integrations/linkedin', () => ({}));
vi.mock('@/lib/integrations/mailboxes', () => ({}));
vi.mock('@/components/app/page', () => ({
  AppPage: ({ topbarActions }: { topbarActions: ReactNode }) => topbarActions,
}));
vi.mock('@/components/campaigns/campaigns-empty', () => ({ CampaignsEmpty: () => null }));
vi.mock('@/components/campaigns/campaigns-index', () => ({ CampaignsIndex: () => null }));
vi.mock('@/components/campaigns/new-campaign-dialog', () => ({
  NewCampaignDialog: ({
    lists,
    defaultListId,
  }: {
    lists: { id: string }[];
    defaultListId?: string;
  }) => `offers ${lists.map((l) => l.id).join(',')} asks for ${defaultListId ?? 'none'}`,
}));
vi.mock('@/components/campaigns/switch-client-on-mount', () => ({
  SwitchClientOnMount: ({ client }: { client: string }) => `switches to ${client}`,
}));

const { default: CampaignsPage } = await import('./page');

async function open(list?: string): Promise<string> {
  const page = await CampaignsPage({ searchParams: Promise.resolve({ list }) });
  return renderToStaticMarkup(page).replace(/<[^>]+>/g, '');
}

describe('/campaigns?list=', () => {
  beforeEach(() => {
    state.active = BETA;
  });

  it("switches to the list's client, without the dialog, when it is another client's person list", async () => {
    expect(await open(ALPHA_PEOPLE)).toBe(`switches to ${ALPHA}`);
  });

  it('asks the dialog for the list once its client is the active one', async () => {
    state.active = ALPHA;
    expect(await open(ALPHA_PEOPLE)).toBe(`offers ${ALPHA_PEOPLE} asks for ${ALPHA_PEOPLE}`);
  });

  it.each([
    ['a list of the active client', BETA_PEOPLE],
    ["another client's company list", ALPHA_COMPANIES],
    ['a list of the active client that its index leaves out', BETA_OLD],
    ['a list the organization does not have', UNKNOWN],
  ])('keeps the active client for %s', async (_, list) => {
    expect(await open(list)).toBe(`offers ${BETA_PEOPLE} asks for ${list}`);
  });
});
