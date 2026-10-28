'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { authError, useErrorText } from '@/i18n/error-text';
import { authClient } from '@/lib/auth-client';
import { setActiveProjectAction } from '@/lib/projects/actions';

/** Creates a team (#676) and moves into it, as the switcher does: Better Auth makes it the active one and the
 *  server sets its slug and empty wallet (lib/team.ts). */
export function NewTeamDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations('nav');
  const errorText = useErrorText();
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    // The server replaces the slug; Better Auth only requires one.
    const { error: failed } = await authClient.organization.create({
      name: name.trim(),
      slug: crypto.randomUUID(),
    });
    if (failed) {
      setBusy(false);
      return setError(errorText(authError(failed)));
    }
    // Created: a failed cookie clear only leaves the old client's id, which the project switcher replaces.
    await setActiveProjectAction(null).catch(() => {});
    // Busy until the page goes: a second submit would make a second team.
    window.location.assign('/');
  };

  // A closed dialog forgets what was typed and the last error.
  const toggle = (next: boolean) => {
    if (!next) {
      setName('');
      setError(null);
    }
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={toggle}>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <DialogHeader>
            <DialogTitle>{t('newTeam')}</DialogTitle>
            <DialogDescription>{t('newTeamBody')}</DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={name}
            maxLength={120}
            aria-label={t('newTeamName')}
            placeholder={t('newTeamName')}
            onChange={(e) => setName(e.target.value)}
          />
          {error && (
            <p role="alert" className="text-negative text-sm">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={busy || !name.trim()}>
              {t('newTeamCreate')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
