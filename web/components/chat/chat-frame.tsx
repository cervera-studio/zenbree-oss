'use client';

import { AnimatePresence, motion } from 'motion/react';
import { useTranslations } from 'next-intl';
import { type ReactNode, useState } from 'react';
import { AppPage } from '@/components/app/page';
import { PanelSettled } from '@/components/lists/panel-settled';
import type { ListEntity } from '@/lib/chat/chat-lists';
import type { ChatView, PanelChoice } from '@/lib/chat/chat-view';
import { cn } from '@/lib/utils';
import { ChatHeader } from './chat-header';

const SLIDE = { hidden: { opacity: 0, x: 24 }, shown: { opacity: 1, x: 0 } };

/**
 * A chat's page: the topbar with its path, status and lists, then the thread. With a list open the
 * thread is 380 px wide and the pane takes the rest; under `lg` the pane covers the thread, as on a phone.
 */
export function ChatFrame({
  view,
  failed,
  phone,
  openEntity,
  onChoose,
  banner,
  thread,
  panel,
}: {
  view: ChatView | null;
  failed: boolean;
  phone: boolean;
  openEntity: ListEntity | null;
  onChoose: (choice: PanelChoice) => void;
  banner: ReactNode;
  thread: ReactNode;
  panel: ReactNode;
}) {
  const t = useTranslations('chat');
  const tNav = useTranslations('nav');
  // A panel already open on load does not slide (`initial={false}`), so it starts settled.
  const [settled, setSettled] = useState(true);
  const header = view && (
    <ChatHeader
      view={view}
      openEntity={openEntity}
      placement={phone ? 'strip' : 'topbar'}
      onToggleList={(entity) =>
        onChoose(openEntity === entity ? { kind: 'closed' } : { kind: 'open', entity })
      }
    />
  );
  return (
    <div className="flex h-full min-h-0 flex-col">
      <AppPage
        crumbs={[
          { label: tNav('searches'), href: '/chats' },
          ...(failed ? [] : [{ label: view ? (view.title ?? t('untitled')) : '…' }]),
        ]}
        crumbsLabel={t('breadcrumb')}
        topbarActions={phone ? undefined : header}
        className="min-h-0 flex-1 gap-0 p-0 phone:p-0"
      >
        {banner}
        {phone && header}
        <div className="relative flex min-h-0 flex-1">
          <div
            className={cn(
              'flex min-w-0 flex-1 flex-col',
              openEntity && 'lg:w-thread lg:flex-none lg:border-r',
            )}
          >
            <div className="min-h-0 flex-1">{thread}</div>
          </div>
          <AnimatePresence initial={false}>
            {openEntity && view && (
              <motion.section
                key="chat-list-panel"
                id="chat-list-panel"
                variants={SLIDE}
                initial="hidden"
                animate="shown"
                exit="hidden"
                onAnimationStart={(variant) => variant === 'shown' && setSettled(false)}
                onAnimationComplete={() => setSettled(true)}
                className="bg-ground absolute inset-0 z-20 lg:static lg:inset-auto lg:min-w-0 lg:flex-1"
              >
                <PanelSettled value={settled}>{panel}</PanelSettled>
              </motion.section>
            )}
          </AnimatePresence>
        </div>
      </AppPage>
    </div>
  );
}
