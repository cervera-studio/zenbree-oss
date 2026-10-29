'use client';

import { Eye, MoreHorizontal } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type ComponentProps, useState } from 'react';
import {
  type ActionResult,
  banUser,
  impersonate,
  revokeSessions,
  setRole,
  unbanUser,
} from '@/app/admin/actions';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { asResult } from '@/lib/admin/as-result';
import { useAction } from '@/lib/hooks/use-action';
import { hasRole } from '@/lib/roles';

/** One lookup for the two trigger looks: a bare row icon, or the bordered square of a page header. */
const TRIGGER = {
  row: { variant: 'ghost', size: 'icon-sm' },
  header: { variant: 'outline', size: 'icon-sm' },
} as const;

export function UserActions({
  user,
  look = 'row',
  jobsHref,
}: {
  look?: keyof typeof TRIGGER;
  user: { id: string; email: string; role?: string | null; banned?: boolean | null };
  /** Adds a «Jobs» item that opens this link (the detail page's view of the user's jobs). */
  jobsHref?: string;
}) {
  const t = useTranslations('admin');
  const { pending, run } = useAction();
  const [banOpen, setBanOpen] = useState(false);
  const [reason, setReason] = useState('');
  // Role changes and session revocation are shared-impact/irreversible: they confirm first.
  const [confirm, setConfirm] = useState<'role' | 'sessions' | null>(null);
  const isAdmin = hasRole(user.role, 'admin');

  const act = (fn: () => Promise<ActionResult>) =>
    run(() => asResult(fn()), {
      successToast: t('common.saved'),
      errorToast: (error) => error.message,
    });

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button {...TRIGGER[look]} disabled={pending} aria-label={t('userList.actions')}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {jobsHref && (
            <>
              <DropdownMenuItem asChild>
                <Link href={jobsHref}>{t('userDetail.jobs')}</Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
            </>
          )}
          <DropdownMenuItem onSelect={() => setConfirm('role')}>
            {isAdmin ? t('userList.makeUser') : t('userList.makeAdmin')}
          </DropdownMenuItem>
          {user.banned ? (
            <DropdownMenuItem onSelect={() => act(() => unbanUser(user.id))}>
              {t('userList.unban')}
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onSelect={() => setBanOpen(true)}>
              {t('userList.ban')}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => setConfirm('sessions')}>
            {t('userList.revokeSessions')}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => act(() => impersonate(user.id))}>
            {t('userList.impersonate')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <ConfirmDialog
        open={confirm === 'role'}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={t(isAdmin ? 'userList.makeUserTitle' : 'userList.makeAdminTitle', {
          email: user.email,
        })}
        description={t(isAdmin ? 'userList.makeUserBody' : 'userList.makeAdminBody')}
        confirmLabel={isAdmin ? t('userList.makeUser') : t('userList.makeAdmin')}
        destructive={isAdmin}
        onConfirm={() => act(() => setRole(user.id, isAdmin ? 'user' : 'admin'))}
      />
      <ConfirmDialog
        open={confirm === 'sessions'}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={t('userList.revokeSessionsTitle', { email: user.email })}
        description={t('userList.revokeSessionsBody')}
        confirmLabel={t('userList.revokeSessions')}
        onConfirm={() => act(() => revokeSessions(user.id))}
      />

      <Dialog open={banOpen} onOpenChange={setBanOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('userList.banTitle', { email: user.email })}</DialogTitle>
            <DialogDescription>{t('userList.banDescription')}</DialogDescription>
          </DialogHeader>
          <Label htmlFor={`ban-${user.id}`}>
            {t('userList.banReason')}{' '}
            <span className="text-muted font-normal">{t('userList.banReasonHint')}</span>
          </Label>
          <Textarea
            id={`ban-${user.id}`}
            placeholder={t('userList.banReasonPlaceholder')}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setBanOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="negative"
              disabled={pending}
              onClick={() => {
                setBanOpen(false);
                act(() => banUser(user.id, reason.trim()));
              }}
            >
              {t('userList.ban')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function ImpersonateButton({
  userId,
  label,
  className,
  size,
}: {
  userId: string;
  label?: string;
  className?: string;
  size?: ComponentProps<typeof Button>['size'];
}) {
  const t = useTranslations('admin');
  const { pending, run } = useAction();
  return (
    <Button
      variant="outline"
      size={size}
      className={className}
      disabled={pending}
      onClick={() =>
        run(() => asResult(impersonate(userId)), { errorToast: (error) => error.message })
      }
    >
      <Eye aria-hidden /> {label ?? t('userList.impersonate')}
    </Button>
  );
}
