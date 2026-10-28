'use client';

import { Menu, X } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { type ReactNode, useState } from 'react';
import { Logo } from '@/components/logo';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { NotificationBell } from './notification-bell';
import { usePhone } from './use-phone';

const iconButton =
  'hover:bg-hair focus-visible:ring-accent/50 flex size-11 shrink-0 items-center justify-center rounded-md outline-none focus-visible:ring-3';

/** Below 760 px (#171): a top bar with the menu, the bell and the credits; the sidebar opens as a
 *  drawer. `children` is the drawer's sidebar. */
export function MobileNav({ credits, children }: { credits: ReactNode; children: ReactNode }) {
  const t = useTranslations('nav');
  const [open, setOpen] = useState(false);
  const phone = usePhone();
  if (open && !phone) setOpen(false);

  return (
    <header className="bg-ground flex h-14 shrink-0 items-center gap-2 border-b px-2 desktop:hidden">
      <Dialog open={open} onOpenChange={setOpen}>
        {/* The Radix trigger is what hands focus back to the button when the drawer closes. */}
        <DialogPrimitive.Trigger className={iconButton} aria-label={t('menu')}>
          <Menu className="size-5" />
        </DialogPrimitive.Trigger>
        <Link href="/" className="flex h-11 min-w-0 flex-1 items-center">
          <Logo />
        </Link>
        <NotificationBell phone />
        {credits}
        <DialogContent
          showCloseButton={false}
          aria-describedby={undefined}
          className="bg-side data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left top-0 left-0 flex h-dvh w-75 max-w-5/6 translate-x-0 translate-y-0 flex-col gap-4 rounded-none border-0 px-3 py-4 shadow-lg data-[state=closed]:zoom-out-100 data-[state=open]:zoom-in-100"
        >
          {/* The workspace row below carries the mark, so the title is for assistive technology. */}
          <div className="flex shrink-0 items-center justify-end">
            <DialogTitle className="sr-only">{t('primary')}</DialogTitle>
            <DialogPrimitive.Close className={iconButton} aria-label={t('closeMenu')}>
              <X className="size-5" />
            </DialogPrimitive.Close>
          </div>
          {/* Following a link closes the drawer; account menu links too, since React events
              bubble out of portals. */}
          {/* biome-ignore lint/a11y/useKeyWithClickEvents: Enter on a link fires click */}
          <nav
            aria-label={t('primary')}
            className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto"
            onClick={(e) => {
              if ((e.target as Element).closest('a')) setOpen(false);
            }}
          >
            {children}
          </nav>
        </DialogContent>
      </Dialog>
    </header>
  );
}
