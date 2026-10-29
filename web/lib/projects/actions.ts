'use server';

import { refresh } from 'next/cache';
import { cookies } from 'next/headers';
import { z } from 'zod';
import { PROJECT_COOKIE } from './cookie';

/** Makes `id` the active project (`null` forgets it: the API picks the default one), then re-renders the page,
 * so server components and the onboarding gate read the new one. */
export async function setActiveProjectAction(id: string | null) {
  const jar = await cookies();
  if (id && z.uuid().safeParse(id).success) {
    jar.set(PROJECT_COOKIE, id, { path: '/', maxAge: 60 * 60 * 24 * 365, sameSite: 'lax' });
  } else {
    jar.delete(PROJECT_COOKIE);
  }
  refresh();
}
