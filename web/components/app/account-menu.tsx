'use client';

import { Building2, Check, Ellipsis, LogOut, Plus } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { type ReactNode, useState } from 'react';
import { Avatar } from '@/components/ui/avatar';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { storedLocale } from '@/i18n/config';
import { authClient, signOut } from '@/lib/auth-client';
import { setActiveProjectAction } from '@/lib/projects/actions';
import { cn } from '@/lib/utils';
import { navEntries, rowFocus } from './nav';
import { NewTeamDialog } from './new-team-dialog';

export type Viewer = {
  id: string;
  name: string;
  email: string;
  image: string | null;
  company: string | null;
  isAdmin: boolean;
  /** Admin of the active organization (#308). */
  isOrgAdmin: boolean;
  /** The organizations the user belongs to; the switcher shows from two up. */
  orgs: { id: string; name: string }[];
  activeOrgId: string | null;
};

// Every list, chat and campaign belongs to the active organization: a full load re-reads them all.
// The active project (client) is the old organization's: forgotten, the new one opens on its default.
function switchOrg(id: string, activeId: string | null) {
  if (id === activeId) return;
  void authClient.organization.setActive({ organizationId: id }).then(async ({ error }) => {
    if (error) return;
    await setActiveProjectAction(null);
    window.location.assign('/');
  });
}

/** The sidebar's foot: who is signed in (a link to the profile) and the account menu. `credits`:
 *  the phone drawer's balance, after the company. */
export function AccountMenu({ viewer, credits }: { viewer: Viewer; credits: ReactNode }) {
  const t = useTranslations('nav');
  const locale = storedLocale(useLocale());
  const router = useRouter();
  const [creatingTeam, setCreatingTeam] = useState(false);
  // pt-1 plus the link's 6 px padding leaves the design's 10 px between the credits card and the avatar.
  return (
    <div className="flex items-center gap-0.5 rounded-md pt-1">
      <Link
        href="/settings/profile"
        title={t('yourProfile')}
        className={cn(
          'hover:bg-hair flex min-w-0 flex-1 items-center gap-2 rounded-md p-1.5',
          rowFocus,
        )}
      >
        <Avatar user={viewer} size="md" tone="positive" image={viewer.image} />
        <span className="flex min-w-0 flex-col">
          <span className="text-ui-strong truncate">{viewer.name}</span>
          <span className="text-small text-muted truncate">
            {viewer.isOrgAdmin ? t('roleAdmin') : viewer.company || viewer.email}
            {credits}
          </span>
        </span>
      </Link>
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label={t('accountMenu')}
          className="text-muted hover:bg-hair hover:text-ink data-[state=open]:bg-hair focus-visible:ring-accent mr-1 flex size-8 shrink-0 items-center justify-center rounded-sm outline-none focus-visible:ring-2"
        >
          <Ellipsis className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="end" className="w-56 rounded-lg p-1.5">
          <div className="mb-1 flex flex-col gap-0.5 border-b px-2.5 pt-2 pb-2.5">
            <span className="text-xs-plus truncate font-semibold">{viewer.name}</span>
            <span className="text-muted truncate text-xs">{viewer.email}</span>
          </div>
          {navEntries('account', viewer.isAdmin, viewer.isOrgAdmin, false, locale).map(
            ({ id, href, icon: Icon, label, adminOnly }) => (
              <DropdownMenuItem key={id} asChild className="text-xs-plus h-8.5">
                <Link href={href}>
                  <Icon />
                  <span className="flex-1">{t(label)}</span>
                  {adminOnly && (
                    <span className="bg-ink text-ground text-3xs rounded-full px-1.5 font-bold">
                      {t('adminBadge')}
                    </span>
                  )}
                </Link>
              </DropdownMenuItem>
            ),
          )}
          {viewer.orgs.length > 1 && (
            <>
              <DropdownMenuSeparator />
              <p className="text-2xs text-muted px-2.5 py-1 font-mono uppercase">
                {t('switchOrg')}
              </p>
              {viewer.orgs.map((org) => (
                <DropdownMenuItem
                  key={org.id}
                  className="text-xs-plus h-8.5"
                  aria-current={org.id === viewer.activeOrgId ? 'true' : undefined}
                  onSelect={() => switchOrg(org.id, viewer.activeOrgId)}
                >
                  <Building2 />
                  <span className="flex-1 truncate">{org.name}</span>
                  {org.id === viewer.activeOrgId && <Check />}
                </DropdownMenuItem>
              ))}
            </>
          )}
          <DropdownMenuItem className="text-xs-plus h-8.5" onSelect={() => setCreatingTeam(true)}>
            <Plus /> {t('newTeam')}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-xs-plus h-8.5"
            onSelect={() => signOut().then(() => router.push('/auth/sign-in'))}
          >
            <LogOut /> {t('signOut')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <NewTeamDialog open={creatingTeam} onOpenChange={setCreatingTeam} />
    </div>
  );
}
