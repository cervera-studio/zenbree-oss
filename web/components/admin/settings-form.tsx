'use client';

import { useTranslations } from 'next-intl';
import { useState, useTransition } from 'react';
import { type ActionResult, addSetting, setSetting } from '@/app/admin/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { Switch } from '@/components/ui/switch';
import { switchEffect } from '@/lib/admin/switch-effect';

async function save(fn: () => Promise<ActionResult>, saved: string) {
  const res = await fn();
  if (res.ok) toast.success(saved);
  else toast.error(res.error);
  return res;
}

// Production: a switch applies to every user within 30 s, so it asks first.
export function SettingSwitch({ settingKey, value }: { settingKey: string; value: boolean }) {
  const t = useTranslations('admin');
  const [next, setNext] = useState<boolean | null>(null);
  return (
    <>
      <Switch tone="warning" aria-label={settingKey} checked={value} onCheckedChange={setNext} />
      <ConfirmDialog
        open={next !== null}
        onOpenChange={(open) => !open && setNext(null)}
        title={t(next ? 'settings.confirmOnTitle' : 'settings.confirmOffTitle', {
          key: settingKey,
        })}
        description={
          next
            ? t(`settings.confirmOnBody.${switchEffect(settingKey)}`)
            : t('settings.confirmOffBody')
        }
        confirmLabel={t(next ? 'settings.confirmOn' : 'settings.confirmOff')}
        destructive={next === true}
        onConfirm={() => save(() => setSetting(settingKey, next), t('common.saved'))}
      />
    </>
  );
}

const INLINE = {
  'ADMIN.INVALID_SETTING_KEY': 'settings.invalidKey',
  'ADMIN.SETTING_EXISTS': 'settings.exists',
} as const;

// New keys start off (false), and the API refuses an existing one: adding never changes a switch.
export function AddSetting() {
  const t = useTranslations('admin');
  const [key, setKey] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        start(async () => {
          const res = await addSetting(key.trim());
          if (res.ok) {
            toast.success(t('common.saved'));
            setKey('');
          } else if (Object.hasOwn(INLINE, res.code)) {
            setError(t(INLINE[res.code as keyof typeof INLINE]));
          } else {
            toast.error(res.error);
          }
        });
      }}
    >
      <div className="flex gap-2">
        <Input
          required
          value={key}
          aria-label={t('settings.keyLabel')}
          aria-invalid={error !== null}
          aria-describedby={error ? 'add-setting-error' : undefined}
          placeholder={t('settings.addPlaceholder')}
          className="text-data font-mono"
          onChange={(e) => {
            setKey(e.target.value);
            setError(null);
          }}
        />
        <Button type="submit" disabled={pending}>
          {t('settings.add')}
        </Button>
      </div>
      {error && (
        <p id="add-setting-error" role="alert" className="text-negative text-sm">
          {error}
        </p>
      )}
    </form>
  );
}
