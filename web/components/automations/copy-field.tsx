'use client';

import { Check, Copy } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCopy } from '@/lib/hooks/use-copy';

/** A read-only value with a copy button. The status line is a live region, so a screen reader
 *  hears that the copy worked or what to do when it did not. */
export function CopyField({
  label,
  value,
  autoFocus = false,
}: {
  label: string;
  value: string;
  autoFocus?: boolean;
}) {
  const t = useTranslations('automations.webhook');
  const id = useId();
  const { copy, copied } = useCopy();
  const [failed, setFailed] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-ui-strong">
        {label}
      </label>
      <div className="flex gap-2">
        <Input
          id={id}
          readOnly
          value={value}
          autoFocus={autoFocus}
          className="font-mono text-data min-w-0 phone:h-11"
          onFocus={(event) => event.target.select()}
        />
        <Button
          type="button"
          variant="outline"
          className="shrink-0 phone:h-11"
          aria-label={t('copy', { what: label })}
          onClick={async () => setFailed(!(await copy(value)))}
        >
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
          <span className="phone:sr-only">{copied ? t('copied') : t('copyShort')}</span>
        </Button>
      </div>
      <p aria-live="polite" className="text-small">
        {failed ? (
          <span className="text-negative">{t('copyFailed')}</span>
        ) : copied ? (
          <span className="text-positive">{t('copiedWhat', { what: label })}</span>
        ) : (
          ''
        )}
      </p>
    </div>
  );
}
