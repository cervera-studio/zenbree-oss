import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const ALPHA = '0b0e7c9e-3f43-4d07-8d86-1a1f2f0f6a11';
const BETA = '5d2c1a47-9e1b-4c55-b1f0-7f3e2a6c9d20';

const api = vi.hoisted(() => ({ projectsUp: true }));

const LISTS = [
  { id: 'alpha-people', name: 'Alpha people', entity: 'person', projectId: ALPHA },
  { id: 'beta-people', name: 'Beta people', entity: 'person', projectId: BETA },
  { id: 'beta-companies', name: 'Beta companies', entity: 'company', projectId: BETA },
];

vi.mock('server-only', () => ({}));
vi.mock('@/lib/auth-token', () => ({ getToken: async () => undefined }));
vi.mock('next/headers', () => ({
  cookies: async () => ({
    get: (name: string) => (name === 'zb-project' ? { value: BETA } : undefined),
  }),
}));
vi.mock('@/lib/http-client', () => ({
  httpClient: {
    get: async (path: string) => {
      const url = new URL(path, 'http://api.test');
      if (url.pathname === '/v1/projects') {
        if (!api.projectsUp) return { success: false, error: { code: 'INTERNAL', message: '' } };
        const projects = [
          { id: ALPHA, name: 'Alpha' },
          { id: BETA, name: 'Beta' },
        ];
        return { success: true, data: { projects } };
      }
      if (url.pathname === '/v1/lists') {
        const project = url.searchParams.get('project_id');
        return {
          success: true,
          data: { lists: LISTS.filter((l) => !project || l.projectId === project) },
        };
      }
      throw new Error(`unexpected GET ${path}`);
    },
  },
}));

const { getPeopleLists } = await import('./api');

describe('getPeopleLists', () => {
  beforeEach(() => {
    api.projectsUp = true;
  });

  it("offers only the active client's person lists", async () => {
    expect((await getPeopleLists()).map((l) => l.id)).toEqual(['beta-people']);
  });

  it('offers no list when the active client is unknown', async () => {
    api.projectsUp = false;
    expect(await getPeopleLists()).toEqual([]);
  });
});
