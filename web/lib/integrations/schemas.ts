// LinkedIn (Unipile) shapes from docs/contracts-m1.md, after snake→camel.
// The m1-tools routes are not written yet: `riskNotice` and the connect
// response union are assumptions, kept loose on purpose.
import { z } from 'zod';

export const linkedInStatusSchema = z.looseObject({
  connected: z.boolean(),
  accountLabel: z.string().nullish(),
  premium: z.boolean().nullish(),
  riskNotice: z.string().nullish(),
  // #307: the account is the project's; only whoever connected it, or an admin, disconnects it.
  canDisconnect: z.boolean().default(true),
});

// Hosted link `{url}`, or the earlier implementation's credential answer `{connected, checkpoint, accountId}`.
export const linkedInConnectSchema = z
  .looseObject({
    url: z.url().nullish(),
    connected: z.boolean().nullish(),
    checkpoint: z.string().nullish(),
    accountId: z.string().nullish(),
  })
  .refine((d) => d.url || d.connected || d.accountId, 'expected url, connected or accountId');

export type ConnectOutcome =
  | { kind: 'redirect'; url: string }
  | { kind: 'connected' }
  | { kind: 'checkpoint'; accountId: string };

export function toOutcome(d: z.infer<typeof linkedInConnectSchema>): ConnectOutcome {
  if (d.url) return { kind: 'redirect', url: d.url };
  if (d.connected || !d.accountId) return { kind: 'connected' };
  return { kind: 'checkpoint', accountId: d.accountId };
}

export const credentialsSchema = z.object({
  username: z.string().trim().min(1).max(320),
  password: z.string().min(1).max(512),
});

export const checkpointInputSchema = z.object({
  accountId: z.string().min(1).max(200),
  code: z.string().trim().min(1).max(64),
});

// Sending mailboxes (#79): GET /v1/integrations/mailboxes, POST .../{provider}/connect.
export const MAILBOX_PROVIDERS = ['google', 'microsoft'] as const;
export type MailboxProvider = (typeof MAILBOX_PROVIDERS)[number];

export const mailboxesSchema = z.object({
  mailboxes: z.array(
    z.looseObject({
      id: z.string(),
      provider: z.enum(MAILBOX_PROVIDERS),
      connected: z.boolean(),
      status: z.string(),
      accountLabel: z.string().nullish(),
      configured: z.boolean(),
      canDisconnect: z.boolean().default(true),
      // #747: false for a mailbox connected before the bounce read permission
      canReadBounces: z.boolean().default(true),
    }),
  ),
  providers: z.array(z.object({ provider: z.enum(MAILBOX_PROVIDERS), configured: z.boolean() })),
});

export const mailboxConnectSchema = z.object({ authorizationUrl: z.url() });

// The timeline of external activity (#932). `code` stays a string on the wire: a code a newer API adds must
// not blank the card; the card types its codes from the generated API types (hubspot-timeline.tsx).
const timelineIssueSchema = z.object({
  code: z.string(),
  state: z.enum(['waiting', 'skipped', 'failed', 'uncertain']),
  count: z.number(),
  action: z.enum(['retry', 'resolve']).nullable(),
  latestAt: z.string(),
});
const timelineSchema = z.object({
  logged: z.number(),
  pending: z.number(),
  issues: z.array(timelineIssueSchema),
});
export type HubSpotTimeline = z.infer<typeof timelineSchema>;
export const timelineChangedSchema = z.object({ changed: z.number() });

// HubSpot read gate (#120): GET /v1/integrations/hubspot; connect answers like a mailbox.
export const hubspotStatusSchema = z.looseObject({
  connected: z.boolean(),
  status: z.string(),
  accountLabel: z.string().nullish(),
  configured: z.boolean(),
  // #526: the grant may write contacts; companies too (ticked separately at HubSpot); the switch is on.
  canWrite: z.boolean(),
  canWriteCompanies: z.boolean(),
  writeBack: z.boolean(),
  timeline: timelineSchema.default({ logged: 0, pending: 0, issues: [] }),
});
export type HubSpotStatus = z.infer<typeof hubspotStatusSchema>;

// #930: how the campaign events reach Zenbree. `live` only once a webhook delivered: a plan without
// webhooks is `polled`, and the card says why (`webhookRefusal`).
const instantlySyncSchema = z.object({
  mode: z.enum(['live', 'polled', 'paused', 'off']),
  webhook: z.enum(['creating', 'registered', 'live', 'degraded']).nullish(),
  webhookRefusal: z.enum(['plan', 'scope', 'refused', 'no_public_url']).nullish(),
  // The key may not read emails: sends and replies come from the webhook alone, and no reply is classified.
  emailsRefused: z.boolean().nullish(),
  completeUntil: z.string().nullish(),
  lastSuccessAt: z.string().nullish(),
  lastAttemptAt: z.string().nullish(),
  lastErrorCode: z.string().nullish(),
  failingSince: z.string().nullish(),
});
export type InstantlySync = z.infer<typeof instantlySyncSchema>;

// Instantly (#929): GET /v1/integrations/instantly. The key never reaches the web, not even encrypted.
export const instantlyStatusSchema = z.looseObject({
  connected: z.boolean(),
  status: z.enum(['connected', 'error', 'disconnected']),
  name: z.string().nullish(),
  workspaceName: z.string().nullish(),
  statusDetail: z.string().nullish(),
  configured: z.boolean(),
  canManage: z.boolean(),
  sync: instantlySyncSchema.nullish(),
});
export type InstantlyStatus = z.infer<typeof instantlyStatusSchema>;

// #936: GET /v1/integrations/instantly/accounts, Instantly's own sending mailboxes. A label this web build does not
// know reads `unknown`, so a new Instantly value never fails the page.
const ACCOUNT_STATUSES = [
  'active',
  'paused',
  'maintenance',
  'connection_error',
  'soft_bounce_error',
  'sending_error',
  'unknown',
] as const;
const WARMUP_STATUSES = [
  'active',
  'paused',
  'banned',
  'spam_folder_unknown',
  'permanent_suspension',
  'unknown',
] as const;
export const instantlyAccountsSchema = z.object({
  state: z.enum(['ok', 'not_connected', 'no_permission', 'unavailable']),
  accounts: z.array(
    z.object({
      email: z.string(),
      status: z.enum(ACCOUNT_STATUSES).catch('unknown'),
      warmup: z.enum(WARMUP_STATUSES).catch('unknown'),
      dailyLimit: z.number().nullish(),
    }),
  ),
});
export type InstantlyAccount = z.infer<typeof instantlyAccountsSchema>['accounts'][number];

export const instantlyConnectInputSchema = z.object({
  name: z.string().trim().min(1).max(100),
  apiKey: z.string().trim().min(1).max(500),
});

// Senders page (#89): GET /v1/integrations/senders.
const DNS_GRADES = ['pass', 'warn', 'fail', 'unknown', 'managed'] as const;
const dnsGrade = z.enum(DNS_GRADES);

export const sendersSchema = z.object({
  senders: z.array(
    z.looseObject({
      provider: z.enum(MAILBOX_PROVIDERS),
      accountLabel: z.string().nullish(),
      status: z.string(),
      paused: z.boolean(),
      sentToday: z.number(),
      dailyCap: z.number(),
      warmingUp: z.boolean(),
      domain: z.string().nullish(),
      dns: z.object({ spf: dnsGrade, dkim: dnsGrade, dmarc: dnsGrade }).nullish(),
      sendWindowStartHour: z.number(),
      sendWindowEndHour: z.number(),
      sendWindowDays: z.array(z.number()),
      sendTimezone: z.string(),
    }),
  ),
});

// The organization's accounts and the clients using each (#327); `canManage`: the caller connected it or is an admin.
export const senderAccountsSchema = z.object({
  accounts: z.array(
    z.looseObject({
      id: z.string(),
      provider: z.enum([...MAILBOX_PROVIDERS, 'unipile']),
      accountLabel: z.string().nullish(),
      status: z.string(),
      connectedBy: z.string().nullish(),
      projectIds: z.array(z.string()),
      canManage: z.boolean(),
    }),
  ),
});
export type SenderAccount = z.infer<typeof senderAccountsSchema>['accounts'][number];
