import { useTranslations } from 'next-intl';
import { Tag } from '@/components/ui/badge';
import { Select } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { Schema } from '@/lib/api/schema';
import type { IMPORT_TARGETS } from '@/lib/lists/columns';

type Target = (typeof IMPORT_TARGETS)[keyof typeof IMPORT_TARGETS][number];

export function MappingTable({
  preview,
  mapping,
  targets,
  onChange,
}: {
  preview: Schema<'CsvPreview'>;
  mapping: string[];
  targets: readonly string[];
  onChange: (m: string[]) => void;
}) {
  const t = useTranslations('lists');
  return (
    <div className="max-h-80 overflow-auto">
      <Table frame>
        <TableHeader>
          <TableRow>
            <TableHead>{t('importTable.column')}</TableHead>
            <TableHead className="phone:hidden">{t('importTable.sample')}</TableHead>
            <TableHead>{t('importTable.field')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {preview.headers.map((header, i) => (
            <MappingRow
              key={`${i}-${header}`}
              index={i}
              header={header}
              sample={preview.samples[i] ?? ''}
              detected={preview.mapping[i] ?? ''}
              value={mapping[i] ?? ''}
              targets={targets}
              onChange={(v) => onChange(mapping.map((m, j) => (j === i ? v : m)))}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function MappingRow({
  index,
  header,
  sample,
  detected,
  value,
  targets,
  onChange,
}: {
  index: number;
  header: string;
  sample: string;
  detected: string;
  value: string;
  targets: readonly string[];
  onChange: (v: string) => void;
}) {
  const t = useTranslations('lists');
  const customColumn = detected && !targets.includes(detected) ? detected : null;
  const auto = value !== '' && value === detected && !customColumn;
  return (
    <TableRow>
      <TableCell className="text-ink max-w-48 truncate font-bold">
        <label htmlFor={`csv-map-${index}`}>{header || '—'}</label>
      </TableCell>
      <TableCell className="max-w-56 truncate text-small phone:hidden">{sample}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Select
            id={`csv-map-${index}`}
            size="sm"
            className="min-w-0 grow"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          >
            {customColumn && <option value={customColumn}>{t('importOwnColumn')}</option>}
            <option value="">{t('importIgnore')}</option>
            {targets.map((k) => (
              <option key={k} value={k}>
                {t(`importFields.${k as Target}`)}
              </option>
            ))}
          </Select>
          {auto && <Tag tone="positive">{t('importAuto')}</Tag>}
        </div>
      </TableCell>
    </TableRow>
  );
}
