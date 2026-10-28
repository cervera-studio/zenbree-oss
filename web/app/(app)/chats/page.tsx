import { getTranslations } from 'next-intl/server';
import { AllChats } from '@/components/app/all-chats';
import { AppPage } from '@/components/app/page';

export default async function ChatsPage() {
  const [t, tNav] = await Promise.all([getTranslations('chat'), getTranslations('nav')]);
  return (
    <AppPage crumbs={[{ label: tNav('searches') }]} crumbsLabel={t('breadcrumb')}>
      <div className="mx-auto w-full max-w-4xl">
        <AllChats />
      </div>
    </AppPage>
  );
}
