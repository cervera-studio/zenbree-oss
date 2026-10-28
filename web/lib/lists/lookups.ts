/**
 * What a row's email and phone lookup show (#659). The API sends the state (`lookups` on every row, typed in
 * `schema.gen.ts` from `api/app/lists/lookups.py`); the tables below are keyed by its generated unions, so a state the
 * API adds fails `pnpm typecheck` here until it has a row.
 */
import { z } from 'zod';
import type { Tone } from '@/components/ui/badge';
import type { Schema } from '@/lib/api/schema';

type Lookups = Schema<'Lookups'>;
type EmailLookup = Lookups['email'];
type PhoneLookup = Lookups['phone'];
type Unfound = Schema<'Unfound'>['state'];
type Verdict = Schema<'EmailFound'>['verdict'];

export interface EmailChip {
  labelKey: `emailChip.${'verified' | 'catch_all' | 'unverified' | 'invalid' | 'missing'}`;
  tone: Tone;
}

/** The chip beside a found address, by the verifier's verdict. */
const VERDICT_CHIP: Record<Verdict, EmailChip> = {
  verified: { labelKey: 'emailChip.verified', tone: 'positive' },
  catch_all: { labelKey: 'emailChip.catch_all', tone: 'warning' },
  mx_ok: { labelKey: 'emailChip.unverified', tone: 'neutral' },
  unknown: { labelKey: 'emailChip.unverified', tone: 'neutral' },
  invalid: { labelKey: 'emailChip.invalid', tone: 'negative' },
};

interface NoEmail {
  textKey:
    | 'emailNotSearched'
    | 'emailSearching'
    | 'emailNotFound'
    | 'emailFailed'
    | 'emailRefunded';
  chip: EmailChip | null;
  /** A running turn is searching for it (the API's `searching`): the cell pulses, on the list page and in the chat. */
  pulse: boolean;
  /** Only a search by row id takes it again (#531): the cell offers to ask the chat. */
  askAgain: boolean;
}

/** A row with no address, by its lookup state. */
const NO_EMAIL: Record<Unfound, NoEmail> = {
  not_searched: { textKey: 'emailNotSearched', chip: null, pulse: false, askAgain: false },
  searching: { textKey: 'emailSearching', chip: null, pulse: true, askAgain: false },
  not_found: {
    textKey: 'emailNotFound',
    chip: { labelKey: 'emailChip.missing', tone: 'neutral' },
    pulse: false,
    askAgain: false,
  },
  failed: { textKey: 'emailFailed', chip: null, pulse: false, askAgain: false },
  refunded: { textKey: 'emailRefunded', chip: null, pulse: false, askAgain: true },
};

const FOUND = { textKey: null, pulse: false, askAgain: false } as const;

/** What the email cell shows for a lookup: its chip, the text in place of a missing address, and its actions. */
export function emailView(lookup: EmailLookup) {
  return lookup.state === 'found'
    ? { ...FOUND, chip: VERDICT_CHIP[lookup.verdict] }
    : NO_EMAIL[lookup.state];
}

export const isVerified = (lookup: EmailLookup) =>
  lookup.state === 'found' && lookup.verdict === 'verified';

interface NoPhone {
  textKey: 'phonePending' | 'phoneNotFound' | 'phoneFailed' | 'phoneRefunded' | null;
  /** «Cerca il telefono» is offered: a final miss offers no paid retry until a found email reopens it (#504). */
  search: boolean;
  /** What the phone action says in place of the search it no longer offers. */
  statusKey: 'phoneNone' | null;
  /** A running turn is searching for it: the cell pulses. */
  pulse: boolean;
}

/** A row with no number, by its lookup state. */
const NO_PHONE: Record<Unfound, NoPhone> = {
  not_searched: { textKey: null, search: true, statusKey: null, pulse: false },
  searching: { textKey: 'phonePending', search: false, statusKey: null, pulse: true },
  not_found: { textKey: 'phoneNotFound', search: false, statusKey: 'phoneNone', pulse: false },
  failed: { textKey: 'phoneFailed', search: true, statusKey: null, pulse: false },
  refunded: { textKey: 'phoneRefunded', search: true, statusKey: null, pulse: false },
};

/** What the phone cell and its action show for a lookup; null once a number is found. */
export const phoneView = (lookup: PhoneLookup): NoPhone | null =>
  lookup.state === 'found' ? null : NO_PHONE[lookup.state];

type RowPhoneStatus = Schema<'RowPhone'>['status'];
export type PhoneAnswer = Exclude<RowPhoneStatus, 'already'>;

/** How a «Cerca il telefono» click reads, by the API's answer: a number that arrived meanwhile is found too. */
export const PHONE_ANSWER: Record<RowPhoneStatus, PhoneAnswer> = {
  found: 'found',
  already: 'found',
  not_found: 'not_found',
  no_input: 'no_input',
  unavailable: 'unavailable',
  failed: 'failed',
};

/** A value that is a key of `table`: the tables above are the web's only list of the API's values. */
const keyOf = <K extends string>(table: Record<K, unknown>) =>
  z.custom<K>((v) => typeof v === 'string' && Object.hasOwn(table, v));

const unfound = z.object({ state: keyOf(NO_EMAIL) });

export const lookupsSchema = z.object({
  email: z.union([z.object({ state: z.literal('found'), verdict: keyOf(VERDICT_CHIP) }), unfound]),
  phone: z.union([z.object({ state: z.literal('found') }), unfound]),
}) satisfies z.ZodType<Lookups>;

export const rowPhoneStatusSchema = keyOf(PHONE_ANSWER);
