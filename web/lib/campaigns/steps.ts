// Campaign steps as the API stores them (`app/outreach/domain/campaign.py`), after snake→camel.
// A channel is just data: the editor renders whichever of these the API accepts.
import { z } from 'zod';

// hubspot_task (#123) sends nothing: it creates a HubSpot task with the draft for the account's owner.
export const CHANNELS = ['email', 'linkedin_connect', 'linkedin_message', 'hubspot_task'] as const;
export type Channel = (typeof CHANNELS)[number];

// `app/outreach/sending.py`: MAX_BODY for messages, the Premium invitation note for an invite.
export const MAX_BODY = {
  email: 4000,
  linkedin_message: 4000,
  linkedin_connect: 300,
  hubspot_task: 4000,
} as const;
export const MAX_STEPS = 12;
// `validate_steps`: every step needs a body except an invite, which may go without a note.
const bodyOptional = (channel: Channel) => channel === 'linkedin_connect';
// The two copies of an A/B test (#934): A is the step's own copy.
export const VARIANTS = ['a', 'b'] as const;
export type Variant = (typeof VARIANTS)[number];

// Variant B's copy of a step (#934). Absent: people in variant B get the step's own copy.
const copySchema = z.object({ subject: z.string(), body: z.string() });
export const stepSchema = z.looseObject({
  step: z.number(),
  channel: z.enum(CHANNELS),
  subject: z.string(),
  body: z.string(),
  sendAfterDays: z.number(),
  threaded: z.boolean().optional(),
  b: copySchema.nullish(),
});
export type Step = z.infer<typeof stepSchema>;

export function blankStep(previous?: Step, channel: Channel = 'email'): Step {
  return {
    step: (previous?.step ?? 0) + 1,
    channel,
    subject: '',
    body: '',
    sendAfterDays: previous ? previous.sendAfterDays + 3 : 0,
    // A follow-up email replies in the first email's thread by default, as in the earlier implementation.
    threaded: channel === 'email' && previous !== undefined && previous.channel === 'email',
  };
}

/** The exact keys the the API's strict validator accepts, numbered 1..n, fields the channel ignores cleared. */
export function toApiSteps(steps: Step[]): Step[] {
  return steps.map((s, i) => {
    const email = s.channel === 'email';
    const threaded = email && Boolean(s.threaded);
    const hasSubject = (email && !threaded) || s.channel === 'hubspot_task';
    const step: Step = {
      step: i + 1,
      channel: s.channel,
      subject: hasSubject ? s.subject : '',
      body: s.body,
      sendAfterDays: Math.max(0, Math.trunc(s.sendAfterDays) || 0),
      threaded,
    };
    // Kept, so that saving from the builder never erases a B copy written through the API. An empty one is
    // «same as A» in the builder, and would go out as an empty copy B; on an invite it is a test of no note.
    const b = s.b && { subject: hasSubject ? s.b.subject : '', body: s.b.body };
    if (b && (b.subject || b.body || bodyOptional(s.channel))) step.b = b;
    return step;
  });
}

/** The the API's `has_test`: a step has a copy B. Pass the builder's steps through `toApiSteps` first. */
export const hasCopyB = (steps: Step[]) => steps.some((s) => s.b);

export const hasLinkedIn = (steps: Step[]) => steps.some((s) => s.channel.startsWith('linkedin'));
