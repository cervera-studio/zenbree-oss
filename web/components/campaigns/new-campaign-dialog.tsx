'use client';

import { Plus } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { NewCampaignForm } from './new-campaign-form';

/** «Nuova campagna da una lista»; a list's «Avvia campagna» (`?list=`, #171) opens it preselected. */
export function NewCampaignDialog({
  lists,
  defaultListId,
}: {
  lists: { id: string; name: string }[];
  defaultListId?: string;
}) {
  const t = useTranslations('campaigns');
  const [requested, setRequested] = useState(defaultListId);
  const [open, setOpen] = useState(Boolean(defaultListId));

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next && requested) {
      setRequested(undefined);
      dropListParamFromUrl();
    }
  }
  return (
    <>
      <Button className="phone:h-11" onClick={() => setOpen(true)}>
        <Plus aria-hidden />
        <span className="phone:sr-only">{t('newFromList')}</span>
      </Button>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-xl" aria-describedby="new-campaign-description">
          <DialogHeader>
            <DialogTitle>{t('new.title')}</DialogTitle>
            <p id="new-campaign-description" className="text-muted text-sm">
              {t('new.description')}
            </p>
          </DialogHeader>
          <NewCampaignForm lists={lists} defaultListId={requested} />
        </DialogContent>
      </Dialog>
    </>
  );
}

function dropListParamFromUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete('list');
  window.history.replaceState(null, '', url);
}
