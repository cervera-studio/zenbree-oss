import type { MESSAGES } from './messages';

declare module 'next-intl' {
  interface AppConfig {
    Messages: (typeof MESSAGES)['en'];
  }
}
