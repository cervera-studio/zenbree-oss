'use client';

import { Check, Copy } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCopy } from '@/lib/hooks/use-copy';

const selectOnMount = (el: HTMLInputElement | null) => el?.select();

/** The signing secret, readable only here: the API never returns it again. */
export function SecretReveal({ secret, onDismiss }: { secret: string; onDismiss: () => void }) {
  const t = useTranslations('webhooks');
  const { copy, copied } = useCopy();
  const [failed, setFailed] = useState(false);

  async function copySecret() {
    setFailed(!(await copy(secret)));
  }

  return (
    <div role="status" className="bg-positive-tint flex flex-col gap-2.5 rounded-lg p-4">
      <p className="text-ui-strong text-positive">{t('secretTitle')}</p>
      <p className="text-small text-soft">{t('secretHelp')}</p>
      <div className="flex gap-2">
        <Input
          ref={selectOnMount}
          aria-label={t('secretField')}
          className="font-mono text-data phone:h-11"
          readOnly
          value={secret}
          onFocus={(event) => event.target.select()}
        />
        <Button
          type="button"
          variant="outline"
          className="phone:h-11"
          aria-label={copied ? t('copied') : t('copySecret')}
          onClick={() => void copySecret()}
        >
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
          {copied ? t('copied') : t('copySecret')}
        </Button>
      </div>
      {failed && (
        <p role="alert" className="text-negative text-small">
          {t('copyFailed')}
        </p>
      )}
      <div>
        <Button type="button" variant="outline" className="phone:h-11" onClick={onDismiss}>
          {t('secretDone')}
        </Button>
      </div>
    </div>
  );
}
