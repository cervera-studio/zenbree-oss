import 'server-only';

import { cookies } from 'next/headers';
import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { PROJECT_COOKIE } from './cookie';
import { oneProjectSchema, projectsSchema } from './schema';

/** The active organization's projects, and the active one: the cookie's, else the oldest (the API's default). */
export async function getProjects() {
  const [res, jar] = await Promise.all([
    httpClient.get('/v1/projects', { token: await getToken(), schema: projectsSchema }),
    cookies(),
  ]);
  if (!res.success) return res;
  const { projects } = res.data;
  const wanted = jar.get(PROJECT_COOKIE)?.value;
  return {
    ...res,
    data: { projects, active: projects.find((p) => p.id === wanted) ?? projects[0] },
  };
}

export async function setProjectCap(
  id: string,
  cap: { amount: number; mode: 'hard' | 'soft' } | null,
) {
  return httpClient.patch(
    `/v1/projects/${encodeURIComponent(id)}`,
    { creditCap: cap?.amount ?? null, creditCapMode: cap?.mode ?? null },
    { token: await getToken(), schema: oneProjectSchema },
  );
}
