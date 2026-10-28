'use server';

import { revalidatePath } from 'next/cache';
import { isUuid } from '@/lib/config/uuid';
import {
  connectHubSpot,
  disconnectHubSpot,
  resolveHubSpotTimeline,
  retryHubSpotTimeline,
  setHubSpotWriteBack,
} from '@/lib/integrations/hubspot';
import { connectInstantly, disconnectInstantly } from '@/lib/integrations/instantly';
import { moveInstantlyStop } from '@/lib/integrations/instantly-stops';
import {
  connectLinkedIn,
  disconnectLinkedIn,
  solveLinkedInCheckpoint,
} from '@/lib/integrations/linkedin';
import { connectMailbox, disconnectMailbox } from '@/lib/integrations/mailboxes';
import {
  type ConnectOutcome,
  checkpointInputSchema,
  credentialsSchema,
  instantlyConnectInputSchema,
  MAILBOX_PROVIDERS,
  type MailboxProvider,
  toOutcome,
} from '@/lib/integrations/schemas';
import { AppError, fail, type Result, succeed } from '@/lib/types';

const PATH = '/settings/integrations';

function invalid<T>(): Result<T> {
  return fail(new AppError('REQUEST.INVALID_INPUT', 'Invalid input', 400));
}

function settle(result: Awaited<ReturnType<typeof connectLinkedIn>>): Result<ConnectOutcome> {
  if (!result.success) return result;
  const outcome = toOutcome(result.data);
  if (outcome.kind === 'connected') revalidatePath(PATH);
  return succeed(outcome);
}

// The password is forwarded and dropped: never stored, never logged.
export async function connectLinkedInAction(
  riskAccepted: boolean,
  credentials?: { username: string; password: string },
): Promise<Result<ConnectOutcome>> {
  if (riskAccepted !== true) return invalid();
  if (!credentials) return settle(await connectLinkedIn());
  const parsed = credentialsSchema.safeParse(credentials);
  return parsed.success ? settle(await connectLinkedIn(parsed.data)) : invalid();
}

export async function linkedInCheckpointAction(
  accountId: string,
  code: string,
): Promise<Result<ConnectOutcome>> {
  const parsed = checkpointInputSchema.safeParse({ accountId, code });
  if (!parsed.success) return invalid();
  return settle(await solveLinkedInCheckpoint(parsed.data.accountId, parsed.data.code));
}

export async function disconnectLinkedInAction(): Promise<Result<unknown>> {
  const result = await disconnectLinkedIn();
  revalidatePath(PATH);
  return result;
}

const isMailbox = (p: unknown): p is MailboxProvider =>
  (MAILBOX_PROVIDERS as readonly unknown[]).includes(p);

// The consent-screen URL; the API binds it to this user through a signed `state`.
export async function connectMailboxAction(provider: MailboxProvider): Promise<Result<string>> {
  if (!isMailbox(provider)) return invalid();
  const result = await connectMailbox(provider);
  return result.success ? succeed(result.data.authorizationUrl) : result;
}

export async function disconnectMailboxAction(senderId: string): Promise<Result<unknown>> {
  if (!isUuid(senderId)) return invalid();
  const result = await disconnectMailbox(senderId);
  revalidatePath(PATH);
  return result;
}

export async function connectHubSpotAction(): Promise<Result<string>> {
  const result = await connectHubSpot();
  return result.success ? succeed(result.data.authorizationUrl) : result;
}

export async function setHubSpotWriteBackAction(enabled: boolean): Promise<Result<boolean>> {
  const result = await setHubSpotWriteBack(enabled === true);
  revalidatePath(PATH);
  return result.success ? succeed(result.data.writeBack) : result;
}

// The API answers 422 for a code or an answer that is not in its request model; the strings here only keep
// a forged call from carrying anything else to it.
export async function retryHubSpotTimelineAction(code: string): Promise<Result<number>> {
  if (typeof code !== 'string') return invalid();
  const result = await retryHubSpotTimeline(code);
  revalidatePath(PATH);
  return result.success ? succeed(result.data.changed) : result;
}

export async function resolveHubSpotTimelineAction(
  code: string,
  as: 'log_again' | 'logged',
): Promise<Result<number>> {
  if (typeof code !== 'string' || (as !== 'log_again' && as !== 'logged')) return invalid();
  const result = await resolveHubSpotTimeline(code, as);
  revalidatePath(PATH);
  return result.success ? succeed(result.data.changed) : result;
}

export async function disconnectHubSpotAction(): Promise<Result<unknown>> {
  const result = await disconnectHubSpot();
  revalidatePath(PATH);
  return result;
}

export async function connectInstantlyAction(input: {
  name: string;
  apiKey: string;
}): Promise<Result<unknown>> {
  const parsed = instantlyConnectInputSchema.safeParse(input);
  if (!parsed.success) return invalid();
  const result = await connectInstantly(parsed.data);
  revalidatePath(PATH);
  return result;
}

export async function disconnectInstantlyAction(): Promise<Result<unknown>> {
  const result = await disconnectInstantly();
  revalidatePath(PATH);
  return result;
}

export async function moveInstantlyStopAction(
  stopId: string,
  verb: 'retry' | 'confirm',
): Promise<Result<unknown>> {
  if (!isUuid(stopId) || (verb !== 'retry' && verb !== 'confirm')) return invalid();
  const result = await moveInstantlyStop(stopId, verb);
  revalidatePath(PATH);
  return result;
}
