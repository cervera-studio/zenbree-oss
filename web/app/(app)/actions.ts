'use server';

import { refresh } from 'next/cache';
import { dismissHomeWelcome } from '@/lib/api/home';
import { getSession } from '@/lib/auth-token';
import { AppError, fail } from '@/lib/types';

export async function dismissWelcomeAction() {
  if (!(await getSession())?.user) {
    return fail(new AppError('AUTH.TOKEN_MISSING', 'Not authenticated', 401));
  }
  const res = await dismissHomeWelcome();
  if (res.success) refresh();
  return res;
}
