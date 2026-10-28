'use client';

import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { type FormEvent, useState, useTransition } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { CellLabel, StackedTable } from '@/components/ui/stacked-table';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { authError, useErrorText } from '@/i18n/error-text';
import { authClient } from '@/lib/auth-client';

type Member = {
  id: string;
  userId: string;
  name: string;
  email: string;
  role: string;
  createdAt: string;
};
type Invitation = { id: string; email: string; role: string; expiresAt: string };

const ROLES = ['member', 'admin'] as const;

/** Members of the active organization; an admin also invites, changes roles and removes (#308). */
export function TeamManager({
  admin,
  selfId,
  founderId,
  members,
  invitations,
}: {
  admin: boolean;
  selfId: string;
  /** Who founded this organization (`founderOf`): they stay its admin, so no role change or removal. */
  founderId: string;
  members: Member[];
  invitations: Invitation[];
}) {
  const t = useTranslations('team');
  const errorText = useErrorText();
  const format = useFormatter();
  const router = useRouter();
  const [error, setError] = useState('');
  const [removing, setRemoving] = useState<Member | null>(null);
  const [pending, startTransition] = useTransition();

  // Every action answers { error } on failure; the page re-reads the organization on success.
  const run = (action: () => Promise<{ error: { code?: string } | null }>) =>
    new Promise<boolean>((resolve) =>
      startTransition(async () => {
        setError('');
        const { error: failed } = await action();
        if (failed) setError(errorText(authError(failed), t('errors.generic')));
        else router.refresh();
        resolve(!failed);
      }),
    );

  return (
    <div className="flex flex-col gap-4">
      {admin && (
        <InviteForm
          pending={pending}
          onInvite={(email, role) =>
            run(() => authClient.organization.inviteMember({ email, role }))
          }
        />
      )}
      {error && (
        <p role="alert" className="text-small text-negative">
          {error}
        </p>
      )}
      <StackedTable frame>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('member')}</TableHead>
            <TableHead scope="col">{t('role')}</TableHead>
            <TableHead scope="col">{t('joined')}</TableHead>
            {admin && (
              <TableHead scope="col">
                <span className="sr-only">{t('actions')}</span>
              </TableHead>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {members.map((m) => (
            <TableRow key={m.id}>
              <TableCell>
                <CellLabel>{t('member')}</CellLabel>
                <span className="text-ui-strong">{m.name || m.email}</span>
                {m.userId === selfId && <span className="text-muted"> {t('you')}</span>}
                <span className="text-small text-muted block">{m.email}</span>
              </TableCell>
              <TableCell className="phone:order-1">
                <CellLabel>{t('role')}</CellLabel>
                {admin && m.userId !== founderId ? (
                  <Select
                    touch
                    size="sm"
                    aria-label={t('roleOf', { name: m.name || m.email })}
                    value={m.role}
                    disabled={pending}
                    className="w-32 phone:w-full"
                    onChange={(e) =>
                      run(() =>
                        authClient.organization.updateMemberRole({
                          memberId: m.id,
                          role: e.target.value as (typeof ROLES)[number],
                        }),
                      )
                    }
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {t(`roles.${r}`)}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Badge tone={m.role === 'admin' ? 'accent' : 'neutral'}>
                    {t(m.role === 'admin' ? 'roles.admin' : 'roles.member')}
                  </Badge>
                )}
              </TableCell>
              <TableCell className="text-muted phone:order-2">
                <CellLabel>{t('joined')}</CellLabel>
                <time dateTime={m.createdAt}>{format.dateTime(new Date(m.createdAt), DAY)}</time>
              </TableCell>
              {admin && (
                <TableCell className="text-right phone:order-3">
                  {m.userId !== selfId && m.userId !== founderId && (
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      className="text-negative"
                      disabled={pending}
                      aria-label={t('removeLabel', { name: m.name || m.email })}
                      onClick={() => setRemoving(m)}
                    >
                      {t('remove')}
                    </Button>
                  )}
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </StackedTable>
      {admin && invitations.length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="text-ui-strong">{t('pendingTitle')}</h3>
          <ul className="border-line bg-ground divide-hair flex flex-col divide-y rounded-lg border">
            {invitations.map((i) => {
              const expired = new Date(i.expiresAt).getTime() < Date.now();
              return (
                <li key={i.id} className="text-ui flex flex-wrap items-center gap-3 px-3 py-2.5">
                  <span className="text-ui-strong grow">{i.email}</span>
                  <span className="text-small text-muted">
                    {t(i.role === 'admin' ? 'roles.admin' : 'roles.member')}
                  </span>
                  <Badge tone={expired ? 'neutral' : 'warning'}>
                    {expired
                      ? t('expired')
                      : t('expires', { date: format.dateTime(new Date(i.expiresAt), DAY) })}
                  </Badge>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={pending}
                    aria-label={t('cancelLabel', { email: i.email })}
                    onClick={() =>
                      run(() => authClient.organization.cancelInvitation({ invitationId: i.id }))
                    }
                  >
                    {t('cancel')}
                  </Button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t('removeTitle', { name: removing?.name || removing?.email || '' })}
        description={t('removeBody')}
        confirmLabel={t('removeConfirm')}
        consequences={[t('removeBullet1'), t('removeBullet2')]}
        onConfirm={() =>
          removing &&
          run(() => authClient.organization.removeMember({ memberIdOrEmail: removing.id })).then(
            () => setRemoving(null),
          )
        }
      />
    </div>
  );
}

function InviteForm({
  pending,
  onInvite,
}: {
  pending: boolean;
  onInvite: (email: string, role: (typeof ROLES)[number]) => Promise<boolean>;
}) {
  const t = useTranslations('team');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<(typeof ROLES)[number]>('member');

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void onInvite(email.trim(), role).then((sent) => sent && setEmail(''));
  }

  return (
    <form
      className="border-line bg-ground flex flex-col gap-3 rounded-lg border px-3 py-4 desktop:flex-row desktop:items-end"
      onSubmit={submit}
    >
      <div className="flex min-w-0 grow flex-col gap-1.5">
        <label className="text-small text-soft" htmlFor="invite-email">
          {t('inviteEmail')}
        </label>
        <Input
          id="invite-email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder={t('inviteEmailPlaceholder')}
          required
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label className="text-small text-soft" htmlFor="invite-role">
          {t('role')}
        </label>
        <Select
          touch
          id="invite-role"
          value={role}
          className="desktop:w-36"
          onChange={(event) => setRole(event.target.value as (typeof ROLES)[number])}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {t(`roles.${r}`)}
            </option>
          ))}
        </Select>
      </div>
      <Button type="submit" disabled={pending || !email.trim()}>
        {pending && <Loader2 className="animate-spin" />}
        {t('invite')}
      </Button>
    </form>
  );
}

const DAY = { day: 'numeric', month: 'short', year: 'numeric' } as const;
