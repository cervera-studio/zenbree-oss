import { FileText } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import type { Schema } from '@/lib/api/schema';
import { IMPORT_TARGETS } from '@/lib/lists/columns';
import { MappingTable } from './csv-mapping-table';

export function MappingStep({
  entity,
  file,
  preview,
  mapping,
  onChange,
  onChangeFile,
}: {
  entity: keyof typeof IMPORT_TARGETS;
  file: File;
  preview: Schema<'CsvPreview'>;
  mapping: string[];
  onChange: (m: string[]) => void;
  onChangeFile: () => void;
}) {
  const t = useTranslations('lists');
  const format = useFormatter();
  const targets: readonly string[] = IMPORT_TARGETS[entity];
  const mb = file.size >= 1_000_000;
  const size = format.number(file.size / (mb ? 1_000_000 : 1000), {
    style: 'unit',
    unit: mb ? 'megabyte' : 'kilobyte',
    maximumFractionDigits: mb ? 1 : 0,
  });
  return (
    <div className="flex flex-col gap-3.5 px-4 pt-4">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <FileText aria-hidden="true" className="text-muted size-4" />
        <strong className="text-ui-strong truncate">{file.name}</strong>
        <span className="text-data-sm text-muted font-mono">
          {t('importFileMeta', { rows: preview.rows, cols: preview.headers.length, size })}
        </span>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onChangeFile}>
          {t('importChangeFile')}
        </Button>
      </div>
      <p className="text-ui">
        {t.rich('importRecognized', {
          found: preview.mapping.filter((k) => targets.includes(k)).length,
          total: preview.headers.length,
          b: (chunks) => <strong>{chunks}</strong>,
        })}
      </p>
      <MappingTable preview={preview} mapping={mapping} targets={targets} onChange={onChange} />
    </div>
  );
}
