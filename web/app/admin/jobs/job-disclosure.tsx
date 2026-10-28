'use client';

import { ChevronDown } from 'lucide-react';
import { type ReactNode, useId, useState } from 'react';
import { TableCell, TableRow } from '@/components/ui/table';

/** One job row and its detail row. A `<details>` cannot span table rows, so a button toggles it. */
export function JobDisclosure({
  id,
  defaultOpen,
  label,
  cells,
  colSpan,
  children,
}: {
  id: string;
  defaultOpen: boolean;
  label: string;
  cells: ReactNode;
  colSpan: number;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panel = useId();
  return (
    <>
      <TableRow id={`job-${id}`} className="scroll-mt-20">
        {cells}
        <TableCell>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={open ? panel : undefined}
            aria-label={label}
            onClick={() => setOpen(!open)}
            className="text-muted flex size-6 items-center justify-center rounded-md"
          >
            <ChevronDown
              className={
                open ? 'size-4 rotate-180 transition-transform' : 'size-4 transition-transform'
              }
              aria-hidden
            />
          </button>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow id={panel} className="bg-raised hover:bg-raised">
          <TableCell colSpan={colSpan} className="whitespace-normal">
            {children}
          </TableCell>
        </TableRow>
      )}
    </>
  );
}
