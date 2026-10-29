import { useSyncExternalStore } from 'react';
import { api } from '@/lib/api/browser';
import { API_ROUTES } from '@/lib/config/api-routes';
import { setActiveProjectAction } from './actions';
import { readProjectCookie } from './cookie';
import { oneProjectSchema as one, projectsSchema } from './schema';

export type { Project } from './schema';

export const projectsApi = {
  list: async () => {
    const res = await api(API_ROUTES.projects.list, {
      schema: projectsSchema,
    });
    return res.success ? { ...res, data: res.data.projects } : res;
  },
  create: (name: string) =>
    api(API_ROUTES.projects.list, { method: 'POST', body: { name }, schema: one }),
  rename: (id: string, name: string) =>
    api(API_ROUTES.projects.one(id), { method: 'PATCH', body: { name }, schema: one }),
  remove: (id: string) => api(API_ROUTES.projects.one(id), { method: 'DELETE' }),
};

// ── Current project: per browser (a cookie, #307), shared by sidebar, chat, lists and the server ──
// ponytail: not in the URL, the `?s=` writers replace the whole query.

const EVENT = 'zenbree:project-change';

export function getCurrentProjectId(): string | null {
  return readProjectCookie();
}

export async function setCurrentProjectId(id: string) {
  if (id === getCurrentProjectId()) return;
  await setActiveProjectAction(id);
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

export function useCurrentProjectId(): string | null {
  return useSyncExternalStore(subscribe, getCurrentProjectId, () => null);
}
