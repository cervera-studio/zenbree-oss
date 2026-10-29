'use client';

import { Upload } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { SAMPLE_CSV } from '@/lib/lists/columns';
import { cn } from '@/lib/utils';
import { ErrorNotice } from './csv-import-chrome';
import type { Entity, Notice } from './csv-import-state';

export function FileStep({
  entity,
  onEntity,
  onFile,
  busy,
  error,
}: {
  entity: Entity;
  onEntity: (e: Entity) => void;
  onFile: (f: File) => void;
  busy: boolean;
  error: Notice | null;
}) {
  const t = useTranslations('lists');
  const [over, setOver] = useState(false);
  return (
    <div className="flex flex-col pt-4">
      <fieldset className="px-4">
        <legend className="text-small mb-2">{t('uploadEntity')}</legend>
        <div className="grid grid-cols-2 gap-2 phone:grid-cols-1">
          {(['person', 'company'] as const).map((e) => (
            <label
              key={e}
              className="has-checked:border-accent has-checked:bg-accent-tint has-focus-visible:ring-accent border-line bg-ground flex cursor-pointer flex-col gap-0.5 rounded-lg border px-3 py-2.5 has-focus-visible:ring-2"
            >
              <input
                type="radio"
                name="csv-entity"
                className="sr-only"
                checked={entity === e}
                onChange={() => onEntity(e)}
              />
              <span className="text-ui-strong">{t(`entity.${e}`)}</span>
              <span className="text-muted text-small">{t(`importEntityHint.${e}`)}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label
        className={cn(
          'has-focus-visible:ring-accent border-line-strong bg-raised mx-4 mt-4 flex h-37.5 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 text-center has-focus-visible:ring-2',
          (over || busy) && 'border-accent bg-accent-tint',
          error && 'border-negative',
        )}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const f = e.dataTransfer.files[0];
          if (f && !busy) onFile(f);
        }}
      >
        <input
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = ''; // picking the same file again still fires
            if (f) onFile(f);
          }}
        />
        <Upload aria-hidden="true" className="text-accent-ink size-5" />
        <span className="text-ui-strong">{busy ? t('uploading') : t('importDrop')}</span>
        <span className="text-muted text-small">{t('csvLimits')}</span>
      </label>
      <ErrorNotice error={error} />
      <a
        href={`data:text/csv;charset=utf-8,${encodeURIComponent(SAMPLE_CSV[entity])}`}
        download={`zenbree-${entity}.csv`}
        className="text-accent-ink text-ui-strong mx-4 mt-4 self-start hover:underline"
      >
        {t('importSample')}
      </a>
    </div>
  );
}
