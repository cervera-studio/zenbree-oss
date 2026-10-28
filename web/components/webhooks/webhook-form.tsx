'use client';

import { Loader2, Plus } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type FormEvent, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { eventLabel } from './labels';

export type WebhookInput = { url: string; label: string; eventNames: string[] };

export function WebhookForm({
  events,
  pending,
  disabled,
  onSubmit,
}: {
  events: string[];
  pending: boolean;
  disabled: boolean;
  onSubmit: (input: WebhookInput) => Promise<boolean>;
}) {
  const t = useTranslations('webhooks');
  const [label, setLabel] = useState('');
  const [url, setUrl] = useState('');
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(events));

  function toggle(name: string, on: boolean) {
    setChosen((current) => {
      const next = new Set(current);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const eventNames = events.filter((name) => chosen.has(name));
    if (await onSubmit({ url: url.trim(), label: label.trim(), eventNames })) {
      setLabel('');
      setUrl('');
    }
  }

  const ready = label.trim() !== '' && url.trim() !== '' && chosen.size > 0;
  return (
    <form
      className="border-line bg-raised flex flex-col gap-4 rounded-lg border p-4"
      onSubmit={submit}
    >
      <div className="flex flex-col gap-4 desktop:flex-row">
        <div className="flex min-w-0 flex-col gap-1.5 desktop:w-1/3">
          <label className="text-small" htmlFor="webhook-label">
            {t('label')}
          </label>
          <Input
            id="webhook-label"
            className="phone:h-11"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder={t('labelPlaceholder')}
            maxLength={120}
            required
          />
        </div>
        <div className="flex min-w-0 grow flex-col gap-1.5">
          <label className="text-small" htmlFor="webhook-url">
            {t('url')}
          </label>
          <Input
            id="webhook-url"
            className="font-mono text-data phone:h-11"
            type="url"
            inputMode="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://hooks.example.com/zenbree"
            maxLength={2000}
            aria-describedby="webhook-url-help"
            required
          />
          <p id="webhook-url-help" className="text-small text-muted">
            {t('urlHelp')}
          </p>
        </div>
      </div>
      <fieldset className="flex flex-col gap-1">
        <legend className="text-small mb-1">{t('eventsLegend')}</legend>
        <div className="flex flex-col gap-1 desktop:flex-row desktop:flex-wrap desktop:gap-x-5">
          {events.map((name) => (
            <Label
              key={name}
              className="text-ui min-h-control-md cursor-pointer gap-2.5 font-normal"
            >
              <Checkbox
                checked={chosen.has(name)}
                onCheckedChange={(on) => toggle(name, on === true)}
              />
              {eventLabel(t, name)}
            </Label>
          ))}
        </div>
      </fieldset>
      <div>
        <Button type="submit" className="phone:h-11" disabled={pending || disabled || !ready}>
          {pending ? <Loader2 className="animate-spin" /> : <Plus aria-hidden />}
          {t('create')}
        </Button>
      </div>
    </form>
  );
}
