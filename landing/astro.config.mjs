import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  site: 'https://zenbree.com',
  vite: { plugins: [tailwindcss()] },
  i18n: {
    locales: ['it', 'en', 'es', 'fr'],
    // The language served without a prefix, at `/`. The product's base language is English:
    // `defaultLocale` in src/content.ts.
    defaultLocale: 'it',
    routing: { prefixDefaultLocale: false },
  },
});
