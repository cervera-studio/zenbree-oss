import 'server-only';

import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import {
  autoTopupSavedSchema,
  autoTopupSchema,
  type BillingProfile,
  billingProfileResponseSchema,
  billingProfileSavedSchema,
  checkoutSchema,
  invoicesSchema,
  packsSchema,
  planChangeSchema,
  plansSchema,
  subscriptionSchema,
  transactionsSchema,
} from './schemas';

export const TRANSACTIONS_PAGE = 50;

export async function getPacks() {
  return httpClient.get('/v1/billing/packs', { token: await getToken(), schema: packsSchema });
}

export async function getTransactions(offset = 0) {
  return httpClient.get('/v1/billing/transactions', {
    token: await getToken(),
    params: { limit: TRANSACTIONS_PAGE, offset },
    schema: transactionsSchema,
  });
}

export async function createCheckout(packId: string) {
  return httpClient.post(
    '/v1/billing/checkout',
    { packId },
    { token: await getToken(), schema: checkoutSchema },
  );
}

export async function getPlans() {
  return httpClient.get('/v1/billing/plans', { token: await getToken(), schema: plansSchema });
}

export async function getSubscription() {
  return httpClient.get('/v1/billing/subscription', {
    token: await getToken(),
    schema: subscriptionSchema,
  });
}

export async function createSubscription(planId: string) {
  return httpClient.post(
    '/v1/billing/subscribe',
    { planId },
    { token: await getToken(), schema: checkoutSchema },
  );
}

// An upgrade starts now and charges the price difference; a downgrade waits for the renewal (#417).
export async function changePlan(planId: string) {
  return httpClient.post(
    '/v1/billing/plan',
    { planId },
    { token: await getToken(), schema: planChangeSchema },
  );
}

// Stripe customer portal: cancel, card, invoices, VAT id. Plan changes are ours (changePlan).
export async function createPortal() {
  return httpClient.post('/v1/billing/portal', undefined, {
    token: await getToken(),
    schema: checkoutSchema,
  });
}

export async function getAutoTopup() {
  return httpClient.get('/v1/billing/auto-topup', {
    token: await getToken(),
    schema: autoTopupSchema,
  });
}

export async function saveAutoTopup(body: { enabled: boolean; packId: string; threshold: number }) {
  return httpClient.put('/v1/billing/auto-topup', body, {
    token: await getToken(),
    schema: autoTopupSavedSchema,
  });
}

export async function getInvoices(startingAfter?: string) {
  return httpClient.get('/v1/billing/invoices', {
    token: await getToken(),
    params: { limit: 20, starting_after: startingAfter },
    schema: invoicesSchema,
  });
}

export async function getBillingProfile() {
  return httpClient.get('/v1/billing/profile', {
    token: await getToken(),
    schema: billingProfileResponseSchema,
  });
}

export async function saveBillingProfile(body: BillingProfile) {
  return httpClient.put('/v1/billing/profile', body, {
    token: await getToken(),
    schema: billingProfileSavedSchema,
  });
}
