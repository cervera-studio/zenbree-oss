'use client';

import { Loader2Icon } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useTheme } from 'next-themes';
import {
  type ExternalToast,
  Toaster as Sonner,
  toast as sonnerToast,
  type ToasterProps,
} from 'sonner';

type Tone = 'message' | 'success' | 'info' | 'warning' | 'error' | 'loading';
type Message = Parameters<typeof sonnerToast>[0];

// P1: how long each tone stays. An action («Annulla») buys time to use it.
// Infinity: an error stays until closed, a loading toast until it is updated by id.
const DURATION_MS: Record<Tone, { plain: number; withAction: number }> = {
  message: { plain: 5000, withAction: 8000 },
  success: { plain: 5000, withAction: 8000 },
  info: { plain: 5000, withAction: 5000 },
  warning: { plain: 5000, withAction: 5000 },
  error: { plain: Number.POSITIVE_INFINITY, withAction: Number.POSITIVE_INFINITY },
  loading: { plain: Number.POSITIVE_INFINITY, withAction: Number.POSITIVE_INFINITY },
};

function show(tone: Tone, message: Message, data: ExternalToast = {}) {
  // One toast at a time: a new one replaces the current one. An id that is already on
  // screen is an update (loading to success), so it stays; any other id is a new toast.
  const isUpdate = sonnerToast.getToasts().some((active) => active.id === data.id);
  if (!isUpdate) sonnerToast.dismiss();
  const { plain, withAction } = DURATION_MS[tone];
  const duration = data.duration ?? (data.action ? withAction : plain);
  const options = { ...data, duration };
  return tone === 'message' ? sonnerToast(message, options) : sonnerToast[tone](message, options);
}

const toast = Object.assign(
  (message: Message, data?: ExternalToast) => show('message', message, data),
  {
    success: (message: Message, data?: ExternalToast) => show('success', message, data),
    info: (message: Message, data?: ExternalToast) => show('info', message, data),
    warning: (message: Message, data?: ExternalToast) => show('warning', message, data),
    error: (message: Message, data?: ExternalToast) => show('error', message, data),
    loading: (message: Message, data?: ExternalToast) => show('loading', message, data),
    dismiss: sonnerToast.dismiss,
  },
);

function Glyph({
  path,
  size = 'size-3.5',
  width = 3,
}: {
  path: string;
  size?: string;
  width?: number;
}) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={size}
    >
      <path d={path} />
    </svg>
  );
}

const CHECK = 'M20 6 9 17l-5-5';
const BANG = 'M12 7v6M12 17h.01';
const INFO = 'M12 11v6M12 7h.01';
const CROSS = 'M18 6 6 18M6 6l12 12';

function Dot({ tone, path }: { tone: string; path: string }) {
  return (
    <span aria-hidden className={`flex size-5.5 items-center justify-center rounded-full ${tone}`}>
      <Glyph path={path} />
    </span>
  );
}

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = 'system' } = useTheme();
  const t = useTranslations('common');

  return (
    <Sonner
      theme={theme as ToasterProps['theme']}
      className="toaster group"
      position="bottom-right"
      visibleToasts={1}
      offset={{ right: 24, bottom: 'var(--toast-bottom)' }}
      mobileOffset={{ left: 16, right: 16, bottom: 'var(--toast-bottom)' }}
      closeButton
      // The toast sizes to its text, but never past the list it sits in: this is the max (440px).
      style={{ '--width': '27.5rem' } as React.CSSProperties}
      icons={{
        success: <Dot tone="bg-positive text-ink dark:text-ground" path={CHECK} />,
        warning: <Dot tone="bg-warning text-ink dark:text-ground" path={BANG} />,
        error: <Dot tone="bg-negative text-on-accent" path={BANG} />,
        info: <Dot tone="bg-accent text-on-accent" path={INFO} />,
        loading: <Loader2Icon aria-hidden className="size-5.5 animate-spin" />,
        close: <Glyph path={CROSS} width={2.2} />,
      }}
      toastOptions={{
        unstyled: true,
        closeButtonAriaLabel: t('dismissNotice'),
        classNames: {
          toast:
            'group bg-ink text-ground dark:bg-raised dark:text-ink dark:border dark:border-line flex w-fit! min-w-80 max-w-110 items-center gap-3 rounded-lg py-3 pr-3 pl-4 text-sm leading-snug shadow-lg',
          content: 'flex-1',
          icon: 'flex shrink-0 items-center',
          actionButton:
            'h-7.5 shrink-0 cursor-pointer rounded-md px-2.5 text-xs-plus font-bold underline underline-offset-3',
          closeButton:
            'order-last static! size-7 shrink-0 translate-0! cursor-pointer rounded-md border-0! bg-transparent! text-ground! dark:text-ink! opacity-70 hover:opacity-100 flex items-center justify-center',
        },
      }}
      {...props}
    />
  );
};

export { Toaster, toast };
