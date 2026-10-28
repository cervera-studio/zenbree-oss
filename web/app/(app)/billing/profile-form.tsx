'use client';

import { useLocale, useTranslations } from 'next-intl';
import { type FormEvent, useState, useTransition } from 'react';
import { SettingsSection } from '@/components/settings/settings-section';
import { StatusNotice } from '@/components/settings/status-notice';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import type { BillingProfile, BillingProfileResponse } from '@/lib/billing/schemas';
import { billingProfileAction } from './actions';

const ADDRESS_FIELDS = ['line1', 'line2', 'city', 'state', 'postalCode'] as const;

export function BillingProfileForm({ data }: { data: BillingProfileResponse }) {
  const t = useTranslations('billing.profile');
  const locale = useLocale();
  const [country, setCountry] = useState(data.profile?.address.country ?? 'IT');
  const [customerType, setCustomerType] = useState<BillingProfile['customerType'] | null>(
    data.profile?.customerType ?? null,
  );
  const [outcome, setOutcome] = useState<{ saved: boolean; error: string | null } | null>(null);
  const [pending, startTransition] = useTransition();
  const taxTypes = data.countries.find((c) => c.code === country)?.taxIdTypes ?? [];
  const kind = customerType ?? (taxTypes.length ? 'business' : 'individual');
  const unsupported = kind === 'business' && taxTypes.length === 0;
  const names = new Intl.DisplayNames([locale], { type: 'region' });
  const collator = new Intl.Collator(locale);
  const countries = data.countries
    .map((c) => ({ code: c.code, label: names.of(c.code) ?? c.code }))
    .sort((a, b) => collator.compare(a.label, b.label));
  const errorKey =
    outcome?.error === 'BILLING.PROFILE_SYNC_FAILED'
      ? 'pending'
      : outcome?.error === 'BILLING.PROFILE_INVALID'
        ? 'invalid'
        : 'error';

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const value = (name: string) => String(form.get(name) ?? '');
    const body: BillingProfile = {
      customerType: kind,
      name: value('name'),
      email: value('email'),
      address: {
        country,
        line1: value('line1'),
        line2: value('line2'),
        city: value('city'),
        state: value('state'),
        postalCode: value('postalCode'),
      },
      fiscalCode: value('fiscalCode'),
      taxIdType: value('taxIdType'),
      taxIdValue: value('taxIdValue'),
      recipientCode: value('recipientCode'),
      pec: value('pec'),
    };
    startTransition(async () => setOutcome(await billingProfileAction(body)));
  }

  return (
    <div id="billing-profile" className="scroll-mt-6">
      <SettingsSection title={t('title')} description={t('description')}>
        <StatusNotice
          tone={!data.profile ? 'neutral' : data.syncStatus === 'pending' ? 'negative' : 'positive'}
        >
          {t(data.profile ? data.syncStatus : 'required')}
        </StatusNotice>
        {outcome && (
          <StatusNotice tone={outcome.saved ? 'positive' : 'negative'}>
            {t(outcome.saved ? 'saved' : errorKey)}
          </StatusNotice>
        )}
        <form onSubmit={submit} className="flex flex-col gap-4">
          <fieldset disabled={pending} className="grid gap-4 desktop:grid-cols-2">
            <label htmlFor="billing-customerType" className="flex flex-col gap-1.5 text-small">
              {t('customerType')}
              <Select
                touch
                id="billing-customerType"
                name="customerType"
                value={kind}
                onChange={(event) =>
                  setCustomerType(event.target.value === 'business' ? 'business' : 'individual')
                }
              >
                <option value="individual">{t('individual')}</option>
                <option value="business">{t('business')}</option>
              </Select>
            </label>
            <label htmlFor="billing-country" className="flex flex-col gap-1.5 text-small">
              {t('country')}
              <Select
                touch
                id="billing-country"
                name="country"
                value={country}
                onChange={(event) => setCountry(event.target.value)}
              >
                {countries.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.label}
                  </option>
                ))}
              </Select>
            </label>
            <label htmlFor="billing-name" className="flex flex-col gap-1.5 text-small">
              {t('name')}
              <Input
                id="billing-name"
                name="name"
                required
                maxLength={200}
                autoComplete="organization"
                defaultValue={data.profile?.name}
              />
            </label>
            <label htmlFor="billing-email" className="flex flex-col gap-1.5 text-small">
              {t('email')}
              <Input
                id="billing-email"
                name="email"
                type="email"
                required
                maxLength={254}
                autoComplete="email"
                defaultValue={data.profile?.email}
              />
            </label>
            {ADDRESS_FIELDS.map((field) => (
              <label
                htmlFor={`billing-${field}`}
                key={field}
                className="flex flex-col gap-1.5 text-small"
              >
                {t(field)}
                <Input
                  id={`billing-${field}`}
                  name={field}
                  required={
                    field === 'line1' ||
                    field === 'city' ||
                    (['IT', 'US', 'CA'].includes(country) &&
                      (field === 'state' || field === 'postalCode'))
                  }
                  maxLength={
                    field === 'postalCode' ? 20 : field === 'line1' || field === 'line2' ? 200 : 100
                  }
                  defaultValue={data.profile?.address[field]}
                />
              </label>
            ))}
            <label htmlFor="billing-fiscalCode" className="flex flex-col gap-1.5 text-small">
              {t(country === 'IT' ? 'fiscalCode' : 'fiscalIdentifier')}
              <Input
                id="billing-fiscalCode"
                name="fiscalCode"
                required={kind === 'individual'}
                maxLength={64}
                defaultValue={data.profile?.fiscalCode}
              />
            </label>
            {kind === 'business' && (
              <>
                <label htmlFor="billing-taxIdType" className="flex flex-col gap-1.5 text-small">
                  {t('taxIdType')}
                  <Select
                    touch
                    id="billing-taxIdType"
                    name="taxIdType"
                    key={country}
                    required
                    defaultValue={
                      data.profile?.address.country === country
                        ? data.profile.taxIdType
                        : taxTypes[0]
                    }
                  >
                    {taxTypes.map((type) => (
                      <option key={type} value={type}>
                        {type === 'eu_vat'
                          ? t('taxIdTypes.eu_vat')
                          : type.replaceAll('_', ' ').toUpperCase()}
                      </option>
                    ))}
                  </Select>
                </label>
                <label htmlFor="billing-taxIdValue" className="flex flex-col gap-1.5 text-small">
                  {t('taxIdValue')}
                  <Input
                    id="billing-taxIdValue"
                    name="taxIdValue"
                    required
                    maxLength={64}
                    defaultValue={data.profile?.taxIdValue}
                  />
                </label>
              </>
            )}
            {country === 'IT' && (
              <label htmlFor="billing-recipientCode" className="flex flex-col gap-1.5 text-small">
                {t('recipientCode')}
                <Input
                  id="billing-recipientCode"
                  name="recipientCode"
                  maxLength={7}
                  pattern="[A-Za-z0-9]{7}"
                  defaultValue={data.profile?.recipientCode}
                />
              </label>
            )}
            <label htmlFor="billing-pec" className="flex flex-col gap-1.5 text-small">
              {t('pec')}
              <Input
                id="billing-pec"
                name="pec"
                type="email"
                maxLength={254}
                defaultValue={data.profile?.pec}
                aria-describedby="billing-pec-note"
              />
            </label>
          </fieldset>
          <p id="billing-pec-note" className="text-muted text-small">
            {t('pecNote')}
          </p>
          <p className="text-muted text-small">{t('addressNote')}</p>
          {unsupported && <StatusNotice tone="negative">{t('unsupported')}</StatusNotice>}
          <Button type="submit" disabled={pending || unsupported} className="self-start">
            {t(pending ? 'saving' : 'save')}
          </Button>
        </form>
      </SettingsSection>
    </div>
  );
}
