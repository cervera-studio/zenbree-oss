import type { Metadata } from 'next';
import { Atkinson_Hyperlegible_Next, IBM_Plex_Mono, Newsreader } from 'next/font/google';
import { NextIntlClientProvider } from 'next-intl';
import { getLocale, getTranslations } from 'next-intl/server';
import type { ReactNode } from 'react';
import { Toaster } from '@/components/ui/sonner';
import { BrowserTimeZone } from '@/i18n/browser-time-zone';
import { type Locale, storedLocale } from '@/i18n/config';
import { Providers } from './providers';
import './globals.css';

// The preview a shared link shows (WhatsApp, Slack, LinkedIn): crawlers carry no session, so every
// link lands on the sign-in page (the app's `/` sends them to the marketing site), so the preview
// describes the product, not the page behind it.
// The image is the landing's card, built from docs/brand/og-card.html.
// Open Graph names a language with its territory; /og-<locale>.png exists for each (docs/brand/og-card.html).
const OG_LOCALES: Record<Locale, string> = { en: 'en_US', it: 'it_IT', es: 'es_ES', fr: 'fr_FR' };

export async function generateMetadata(): Promise<Metadata> {
  const [t, locale] = await Promise.all([getTranslations('public.meta'), getLocale()]);
  const preview = { title: t('title'), description: t('description') };
  const image = { url: `/og-${locale}.png`, width: 1200, height: 630, alt: preview.title };
  return {
    metadataBase: new URL(process.env.BETTER_AUTH_URL ?? 'http://localhost:3000'),
    title: 'Zenbree',
    description: preview.description,
    openGraph: {
      ...preview,
      images: [image],
      type: 'website',
      siteName: 'Zenbree',
      locale: OG_LOCALES[storedLocale(locale)],
    },
    twitter: { ...preview, images: [image], card: 'summary_large_image' },
  };
}

const sans = Atkinson_Hyperlegible_Next({ subsets: ['latin'], variable: '--font-ui-sans' });
// Mono (IDs, error codes, the memory editor) and the serif (the home headline)
// appear on few pages, so they load on demand instead of being preloaded everywhere.
const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  preload: false,
  variable: '--font-ui-mono',
});
const display = Newsreader({
  subsets: ['latin'],
  axes: ['opsz'],
  style: ['normal', 'italic'], // italic: the landing hero's second line
  preload: false,
  variable: '--font-display-serif',
});

export default async function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang={await getLocale()}
      className={`${sans.variable} ${mono.variable} ${display.variable}`}
      suppressHydrationWarning
    >
      <body className="bg-ground text-ink antialiased">
        <Providers>
          <NextIntlClientProvider>
            <BrowserTimeZone>
              {children}
              <Toaster />
            </BrowserTimeZone>
          </NextIntlClientProvider>
        </Providers>
      </body>
    </html>
  );
}
