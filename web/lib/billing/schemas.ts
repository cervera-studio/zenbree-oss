import { z } from 'zod';
import type { Camel } from '@/lib/api/camel';
import type { Schema } from '@/lib/api/schema';

export const packsSchema = z.looseObject({
  packs: z.array(z.looseObject({ id: z.string(), credits: z.number(), priceEurCents: z.number() })),
}) satisfies z.ZodType<Camel<Schema<'Packs'>>>;

export const plansSchema = z.looseObject({
  plans: z.array(
    z.looseObject({
      id: z.string(),
      credits: z.number(), // granted on signup and on every monthly renewal
      priceEurCents: z.number(),
    }),
  ),
}) satisfies z.ZodType<Camel<Schema<'Plans'>>>;

export const subscriptionStateSchema = z.looseObject({
  planId: z.string().nullable(), // null: a price not in BILLING_PLANS
  scheduledPlanId: z.string().nullable(), // a downgrade that starts at currentPeriodEnd
  status: z.string(),
  currentPeriodEnd: z.string().nullable(),
  cancelAt: z.string().nullable(), // when the plan ends; null while it renews
}) satisfies z.ZodType<Camel<Schema<'Subscription-Output'>>>;

export const subscriptionSchema = z.looseObject({
  subscription: subscriptionStateSchema.nullable(),
}) satisfies z.ZodType<Camel<Schema<'MySubscription'>>>;

export const planChangeSchema = z.looseObject({
  result: z.enum(['upgraded', 'payment_required', 'downgrade_scheduled', 'downgrade_canceled']),
  paymentUrl: z.string().nullable(), // payment_required: Stripe's page that takes the difference
}) satisfies z.ZodType<Camel<Schema<'PlanChange'>>>;

export const ledgerEntryShape = {
  id: z.string(),
  type: z.enum([
    'deduction',
    'refund',
    'renewal',
    'purchase',
    'bonus',
    'adjustment',
    'reservation',
  ]),
  status: z.enum(['committed', 'reserved', 'released', 'settling']),
  amount: z.number(), // always > 0 on the wire; the sign comes from `type`
  balanceAfter: z.number(),
  description: z.string().nullable(),
  toolName: z.string().nullable(),
  referenceType: z.string().nullable(), // 'plan_expiry': unused plan credits removed at reset
  createdAt: z.string(),
};

const transactionSchema = z.looseObject({
  ...ledgerEntryShape,
  sessionId: z.string().nullable(), // the chat the credits were spent in
  listId: z.string().nullable(), // the list of an AI column fill
}) satisfies z.ZodType<Camel<Schema<'LinkedTransaction'>>>;

export const transactionsSchema = z.looseObject({
  // Unused plan credits removed at reset arrive as a deduction; they are an expiry, not usage.
  items: z.array(
    transactionSchema.transform((tx) =>
      tx.referenceType === 'plan_expiry' ? { ...tx, type: 'expiry' as const } : tx,
    ),
  ),
  nextOffset: z.number().nullable(),
});

export const checkoutSchema = z.looseObject({ url: z.url() }) satisfies z.ZodType<
  Camel<Schema<'RedirectUrl'>>
>;

export const autoTopupSchema = z.looseObject({
  enabled: z.boolean(),
  packId: z.string().nullable(),
  threshold: z.number(),
  thresholds: z.array(z.number()),
  lastError: z.string().nullable(), // Stripe error code of the charge that turned it off
}) satisfies z.ZodType<Camel<Schema<'AutoTopup'>>>;

/** PUT: `setupUrl` is set when a card must be saved first, then it switches on. */
export const autoTopupSavedSchema = autoTopupSchema.extend({
  setupUrl: z.url().nullable(),
}) satisfies z.ZodType<Camel<Schema<'AutoTopupSaved'>>>;

export type Transaction = z.infer<typeof transactionsSchema>['items'][number];

const DEBITS = new Set(['deduction', 'reservation', 'expiry']);

export function signedAmount(tx: Pick<Transaction, 'type' | 'amount'>): number {
  return DEBITS.has(tx.type) ? -Math.abs(tx.amount) : Math.abs(tx.amount);
}

function eur(cents: number, locale: string, wholeWhenExact: boolean): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'EUR',
    ...(wholeWhenExact && cents % 100 === 0 && { maximumFractionDigits: 0 }),
  }).format(cents / 100);
}

export function formatEurCents(cents: number, locale: string): string {
  return eur(cents, locale, true);
}

export function formatPricePer1000(
  priceEurCents: number,
  credits: number,
  locale: string,
): string | null {
  if (credits <= 0) return null;
  return eur(Math.round((priceEurCents * 1000) / credits), locale, false);
}

export const invoicesSchema = z.looseObject({
  items: z.array(
    z.looseObject({
      id: z.string(),
      number: z.string().nullable(),
      createdAt: z.iso.datetime({ offset: true }),
      status: z.enum(['draft', 'open', 'paid', 'uncollectible', 'void']).nullable(),
      currency: z.string().regex(/^[a-z]{3}$/),
      netAmount: z.number().int().nullable(),
      taxAmount: z.number().int().nullable(),
      totalAmount: z.number().int(),
      pdfUrl: z.url().nullable(),
      hostedUrl: z.url().nullable(),
    }),
  ),
  nextCursor: z.string().nullable(),
  hasCustomer: z.boolean(),
  portalAvailable: z.boolean(),
}) satisfies z.ZodType<Camel<Schema<'Invoices'>>>;

export function formatInvoiceAmount(amount: number, currency: string, locale: string): string {
  const formatter = new Intl.NumberFormat(locale, { style: 'currency', currency });
  // Stripe keeps two decimal charge amounts for ISK and UGX despite their currency display format.
  const decimals = ['isk', 'ugx'].includes(currency.toLowerCase())
    ? 2
    : (formatter.resolvedOptions().maximumFractionDigits ?? 2);
  return formatter.format(amount / 10 ** decimals);
}

const billingProfileSchema = z.object({
  customerType: z.enum(['individual', 'business']),
  name: z.string(),
  email: z.string(),
  address: z.object({
    country: z.string(),
    state: z.string(),
    city: z.string(),
    postalCode: z.string(),
    line1: z.string(),
    line2: z.string(),
  }),
  fiscalCode: z.string(),
  taxIdType: z.string(),
  taxIdValue: z.string(),
  recipientCode: z.string(),
  pec: z.string(),
}) satisfies z.ZodType<Camel<Schema<'BillingProfile'>>>;

export const billingProfileSavedSchema = z.object({
  profile: billingProfileSchema,
  syncStatus: z.enum(['local', 'pending', 'synced']),
}) satisfies z.ZodType<Camel<Schema<'SavedBillingProfile'>>>;

export const billingProfileResponseSchema = billingProfileSavedSchema.extend({
  profile: billingProfileSchema.nullable(),
  countries: z.array(z.object({ code: z.string(), taxIdTypes: z.array(z.string()) })),
}) satisfies z.ZodType<Camel<Schema<'ProfileState'>>>;

export type BillingProfile = z.infer<typeof billingProfileSchema>;
export type BillingProfileResponse = z.infer<typeof billingProfileResponseSchema>;
