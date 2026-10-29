'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import {
  changePlan,
  createCheckout,
  createPortal,
  createSubscription,
  saveAutoTopup,
  saveBillingProfile,
} from '@/lib/billing/api';
import type { BillingProfile } from '@/lib/billing/schemas';

// Plain <form action>: no client JS. Success leaves for Stripe, failure comes
// back with ?checkout=error (redirect is Next's control flow, not an error).
export async function checkoutAction(formData: FormData) {
  const packId = formData.get('packId');
  const result = typeof packId === 'string' && packId ? await createCheckout(packId) : null;
  if (result && !result.success) redirectPaymentError(result.error.code, 'checkout');
  redirect(result?.success ? result.data.url : '/billing?checkout=error');
}

export async function subscribeAction(formData: FormData) {
  const planId = formData.get('planId');
  const result = typeof planId === 'string' && planId ? await createSubscription(planId) : null;
  if (result && !result.success) redirectPaymentError(result.error.code, 'checkout');
  redirect(result?.success ? result.data.url : '/billing?checkout=error');
}

// An upgrade whose payment needs the user (declined card, 3-D Secure) leaves for Stripe's invoice page.
export async function changePlanAction(planId: string) {
  const result = await changePlan(planId);
  if (!result.success) redirect('/billing?plan=error');
  const { result: change, paymentUrl } = result.data;
  if (change === 'payment_required') redirect(paymentUrl ?? '/billing?plan=error');
  redirect(`/billing?plan=${change}`);
}

export async function portalAction() {
  const result = await createPortal();
  redirect(result.success ? result.data.url : '/billing?checkout=error');
}

// Turning it on without a saved card leaves for Stripe to save one first.
export async function autoTopupAction(formData: FormData) {
  const packId = formData.get('packId');
  const threshold = Number(formData.get('threshold'));
  const result =
    typeof packId === 'string' && packId && threshold > 0
      ? await saveAutoTopup({ enabled: formData.get('enabled') === 'on', packId, threshold })
      : null;
  if (!result?.success) redirectPaymentError(result?.error.code, 'autotopup');
  redirect(result.data.setupUrl ?? '/billing?autotopup=saved');
}

function redirectPaymentError(code: string | undefined, source: 'checkout' | 'autotopup'): never {
  if (code === 'BILLING.PROFILE_REQUIRED') redirect('/billing?profile=required#billing-profile');
  if (code === 'BILLING.PROFILE_SYNC_FAILED') redirect('/billing?profile=pending#billing-profile');
  redirect(`/billing?${source}=error`);
}

export async function billingProfileAction(profile: BillingProfile) {
  const result = await saveBillingProfile(profile);
  if (result.success || result.error.code === 'BILLING.PROFILE_SYNC_FAILED') {
    revalidatePath('/billing');
  }
  return result.success ? { saved: true, error: null } : { saved: false, error: result.error.code };
}
