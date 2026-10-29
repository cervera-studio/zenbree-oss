'use client';

import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { type ChangeEvent, type ReactNode, useRef, useState, useTransition } from 'react';
import { Avatar } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { useErrorText } from '@/i18n/error-text';
import { api } from '@/lib/api/browser';
import { authClient } from '@/lib/auth-client';

/** The profile photo with its change and remove buttons (#503), laid out as the design's Identity
 *  card row: the photo, the `children` (who the user is), then the buttons. The API stores the file;
 *  the URL it answers is saved as Better Auth's `user.image`, which the sidebar and this page read. */
export function PhotoControl({
  name,
  email,
  image: saved,
  children,
}: {
  name: string;
  email: string;
  image: string | null;
  children?: ReactNode;
}) {
  const t = useTranslations('settings.account.photo');
  const errorText = useErrorText();
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [image, setImage] = useState(saved);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function change(next: string | null) {
    setImage(next);
    router.refresh();
  }

  function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setError(null);
    startTransition(async () => {
      const sent = await api<{ url: string }>('/v1/me/photo', {
        method: 'PUT',
        file: { body: file, type: file.type },
      });
      if (!sent.success) return setError(errorText(sent.error));
      const { error: saveError } = await authClient.updateUser({ image: sent.data.url });
      if (saveError) return setError(t('error'));
      change(sent.data.url);
    });
  }

  function remove() {
    setError(null);
    startTransition(async () => {
      const removed = await api<null>('/v1/me/photo', { method: 'DELETE' });
      if (!removed.success) return setError(errorText(removed.error));
      const { error: saveError } = await authClient.updateUser({ image: null });
      if (saveError) return setError(t('error'));
      change(null);
    });
  }

  return (
    <>
      <Avatar user={{ name, email }} size="lg" tone="accent" image={image} />
      {children}
      <div className="flex items-center gap-1.5 phone:basis-full">
        <input
          ref={input}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="sr-only"
          aria-label={t('choose')}
          onChange={upload}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => input.current?.click()}
        >
          {pending && <Loader2 className="animate-spin" />}
          {t(image ? 'replace' : 'add')}
        </Button>
        {image && (
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={remove}>
            {t('remove')}
          </Button>
        )}
      </div>
      {/* The design draws no hint; it takes its own line so the row keeps the design's shape. */}
      {error ? (
        <p role="alert" className="text-small text-negative basis-full">
          {error}
        </p>
      ) : (
        <p className="text-small text-muted basis-full">{t('hint')}</p>
      )}
    </>
  );
}
