'use client';

import type * as React from 'react';
import { useRef } from 'react';

import { dragOffset, releaseVelocity, type Sample, shouldDismiss } from '@/lib/sheet-drag';
import { cn } from '@/lib/utils';
import { DialogContent } from './dialog';

const SHEET =
  'data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom top-auto bottom-0 left-0 max-h-4/5 max-w-none translate-x-0 translate-y-0 content-start gap-4 overflow-y-auto rounded-b-none px-4 pt-0 pb-6 data-[state=closed]:zoom-out-100 data-[state=open]:zoom-in-100 sm:max-w-none';

// The offset lives in the `translate` property, not `transform`: the exit keyframes animate
// `transform`, so a sheet released mid-drag slides out from where the finger left it.
function currentOffset(el: HTMLElement): number {
  const value = getComputedStyle(el).translate;
  return value === 'none' ? 0 : Number.parseFloat(value.split(' ')[1] ?? '0') || 0;
}

/**
 * Pulls the sheet down with the finger and closes it on release when the throw would carry it past
 * half its height; otherwise it returns to the top from wherever it is, and can be caught again.
 */
function useDragToDismiss(onDismiss: () => void) {
  const sheet = useRef<HTMLDivElement>(null);
  const drag = useRef<{ start: number; base: number; samples: Sample[] } | null>(null);

  const move = (el: HTMLElement, offset: number) => {
    el.style.translate = `0 ${offset}px`;
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = sheet.current;
    if (!el) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    // Read the on-screen offset, so a sheet caught on its way back does not jump.
    const base = currentOffset(el);
    el.style.transition = 'none';
    move(el, base);
    drag.current = { start: e.clientY, base, samples: [{ y: e.clientY, t: e.timeStamp }] };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = sheet.current;
    const d = drag.current;
    if (!el || !d) return;
    move(el, dragOffset(d.base + e.clientY - d.start, el.offsetHeight));
    d.samples.push({ y: e.clientY, t: e.timeStamp });
    if (d.samples.length > 8) d.samples.shift();
  };

  const release = (e: React.PointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const el = sheet.current;
    const d = drag.current;
    drag.current = null;
    if (!el || !d) return;
    // The release point counts too: a finger that stopped before lifting threw nothing.
    d.samples.push({ y: e.clientY, t: e.timeStamp });
    const offset = currentOffset(el);
    if (!cancelled && shouldDismiss(offset, releaseVelocity(d.samples), el.offsetHeight)) {
      onDismiss();
      return;
    }
    el.style.transition = 'translate var(--transition-duration-out) var(--ease-snap)';
    move(el, 0);
  };

  const onTransitionEnd = (e: React.TransitionEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || e.propertyName !== 'translate' || drag.current) return;
    e.currentTarget.style.transition = '';
    e.currentTarget.style.translate = '';
  };

  return {
    sheet,
    onTransitionEnd,
    handle: {
      onPointerDown,
      onPointerMove,
      onPointerUp: (e: React.PointerEvent<HTMLDivElement>) => release(e, false),
      onPointerCancel: (e: React.PointerEvent<HTMLDivElement>) => release(e, true),
    },
  };
}

/**
 * A dialog that rises from the bottom of a phone screen. Its grab handle is a real one: pull it
 * down to close the sheet, as on iOS. Escape and a tap on the dimmed page still close it too.
 */
function BottomSheetContent({
  className,
  children,
  onDismiss,
  ...props
}: Omit<React.ComponentProps<typeof DialogContent>, 'showCloseButton'> & {
  /** Closes the sheet, as the dialog's `onOpenChange(false)` does. */
  onDismiss: () => void;
}) {
  const { sheet, handle, onTransitionEnd } = useDragToDismiss(onDismiss);
  return (
    <DialogContent
      ref={sheet}
      showCloseButton={false}
      className={cn(SHEET, className)}
      onTransitionEnd={onTransitionEnd}
      {...props}
    >
      <div
        aria-hidden="true"
        data-slot="bottom-sheet-handle"
        className="-mx-4 flex h-7 shrink-0 cursor-grab touch-none items-center justify-center select-none active:cursor-grabbing"
        {...handle}
      >
        <span className="bg-line h-1.25 w-10 rounded-full" />
      </div>
      {children}
    </DialogContent>
  );
}

export { BottomSheetContent };
