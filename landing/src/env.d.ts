/// <reference path="../.astro/types.d.ts" />

interface ImportMetaEnv {
  readonly PUBLIC_APP_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

type ThemePreference = 'system' | 'light' | 'dark';

interface Window {
  theme: { read(): ThemePreference; apply(preference: ThemePreference): void };
}
