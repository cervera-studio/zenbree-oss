import type { ActionResult } from '@/app/admin/actions';
import { AppError, fail, type Result, succeed } from '@/lib/types';

export async function asResult(outcome: Promise<ActionResult>): Promise<Result<void>> {
  const res = await outcome;
  return res.ok ? succeed(undefined) : fail(new AppError('ADMIN.ACTION_FAILED', res.error));
}
