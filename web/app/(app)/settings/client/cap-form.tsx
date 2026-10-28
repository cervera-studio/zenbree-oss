'use client';

import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SegmentedControl } from '@/components/ui/segmented-control';
import { useAction } from '@/lib/hooks/use-action';
import { setCapAction } from './actions';

type Mode = 'none' | 'hard' | 'soft';

export function CapForm({
  projectId,
  amount,
  mode,
}: {
  projectId: string;
  amount: number | null;
  mode: 'hard' | 'soft' | null;
}) {
  const t = useTranslations('clientSettings.cap');
  const router = useRouter();
  const [choice, setChoice] = useState<Mode>(mode ?? 'none');
  const [value, setValue] = useState(amount ? String(amount) : '');
  const { pending, run } = useAction();
  const n = Number(value);
  const valid = choice === 'none' || (Number.isInteger(n) && n > 0);

  return (
    <form
      className="border-hair flex flex-col gap-3.5 border-t pt-3.5"
      onSubmit={(e) => {
        e.preventDefault();
        run(() => setCapAction(projectId, choice === 'none' ? null : { amount: n, mode: choice }), {
          successToast: t('saved'),
          errorToast: t('saveError'),
          onSuccess: () => router.refresh(),
        });
      }}
    >
      <div className="flex flex-col items-start gap-2">
        <span className="text-ui-strong">{t('modeLabel')}</span>
        <SegmentedControl
          label={t('modeLabel')}
          value={choice}
          onChange={setChoice}
          disabled={pending}
          options={[
            { value: 'none', label: t('modes.none') },
            { value: 'soft', label: t('modes.soft') },
            { value: 'hard', label: t('modes.hard') },
          ]}
        />
        <p className="text-small text-muted">{t(`modeHelp.${choice}`)}</p>
      </div>
      {choice !== 'none' && (
        <div className="flex flex-col gap-2">
          <Label htmlFor="cap-amount" className="text-small font-normal text-soft">
            {t('amountLabel')}
          </Label>
          <Input
            id="cap-amount"
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="max-w-48"
          />
        </div>
      )}
      <Button type="submit" size="sm" disabled={pending || !valid} className="self-start">
        {t('save')}
      </Button>
    </form>
  );
}
