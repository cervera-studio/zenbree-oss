'use server';

import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import {
  approveRun,
  cancelRun,
  getRuns,
  makeWebhookSecret,
  runAutomation,
  setEnabled,
} from '@/lib/automations/api';
import { AppError, fail, type Result, succeed } from '@/lib/types';

const id = z.uuid();

function invalid() {
  return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
}

function refresh<T>(automationId: string, result: Result<T>, runId?: string): Result<T> {
  if (result.success) {
    revalidatePath('/automations');
    revalidatePath(`/automations/${automationId}`);
    // A literal path revalidates that page only, not the run page under it.
    if (runId) revalidatePath(`/automations/${automationId}/runs/${runId}`);
  }
  return result;
}

export async function setEnabledAction(automationId: string, enabled: boolean) {
  if (!id.safeParse(automationId).success || typeof enabled !== 'boolean') return invalid();
  return refresh(automationId, await setEnabled(automationId, enabled));
}

/** `alreadyGoing`: the API answered with a run that was queued or running before this click. */
export async function runNowAction(automationId: string) {
  if (!id.safeParse(automationId).success) return invalid();
  // A new run is always `queued`: a queued one is new unless the runs before the request had it.
  const before = await getRuns(automationId);
  const result = await runAutomation(automationId);
  if (!result.success) return result;
  const run = result.data;
  const known = before.success && before.data.runs.some((r) => r.id === run.id);
  return refresh(automationId, succeed({ run, alreadyGoing: run.status === 'running' || known }));
}

export async function cancelRunAction(automationId: string, runId: string) {
  if (!id.safeParse(automationId).success || !id.safeParse(runId).success) return invalid();
  return refresh(automationId, await cancelRun(automationId, runId), runId);
}

export async function approveRunAction(automationId: string, runId: string) {
  if (!id.safeParse(automationId).success || !id.safeParse(runId).success) return invalid();
  return refresh(automationId, await approveRun(automationId, runId), runId);
}

/** The secret goes back to the caller's dialog only; the pages reload to show the new `rotatedAt`. */
export async function makeWebhookSecretAction(automationId: string) {
  if (!id.safeParse(automationId).success) return invalid();
  return refresh(automationId, await makeWebhookSecret(automationId));
}
