import { ArrowRight, Inbox } from 'lucide-react';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { InboxFrame } from './frame';

/** C4b: nothing has come in yet, so the filters and the thread pane would be empty chrome. */
export async function EmptyInbox() {
  const t = await getTranslations('inbox');
  return (
    <InboxFrame>
      <EmptyState
        variant="inbox"
        icon={Inbox}
        title={t('empty')}
        text={t('emptyBody')}
        action={
          <Button asChild size="sm">
            <Link href="/campaigns">
              {t('toCampaigns')} <ArrowRight />
            </Link>
          </Button>
        }
      />
    </InboxFrame>
  );
}
