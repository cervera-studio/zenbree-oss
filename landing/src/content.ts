import { en } from './i18n/en';
import { es } from './i18n/es';
import { fr } from './i18n/fr';
import { it } from './i18n/it';

const app = (import.meta.env.PUBLIC_APP_URL || 'http://localhost:3000').replace(/\/$/, '');

export const links = {
  signIn: `${app}/auth/sign-in`,
  terms: `${app}/terms`,
  privacy: `${app}/privacy`,
  repo: 'https://github.com/cervera-studio/zenbree-oss',
  contact: 'mailto:supporto@zenbree.com',
} as const;

// ponytail: Italian keeps `/`, where it has always been, so no indexed URL moves (#699). English is the
// base language of the product: `x-default` points to it, and the app falls back to it.
export const paths = { it: '/', en: '/en/', es: '/es/', fr: '/fr/' } as const;

export type Locale = keyof typeof paths;

export const locales = Object.keys(paths) as Locale[];

export const defaultLocale: Locale = 'en';

export const warmupBars = [20, 40, 60, 80, 100] as const;

export type Person = {
  initials: string;
  name: string;
  role: string;
  company: string;
  score: number;
};

export type StepArt =
  | { kind: 'brief'; brief: string; question: string; choices: readonly [string, string] }
  | { kind: 'plan'; title: string; budget: string; steps: readonly string[]; note: string }
  | {
      kind: 'list';
      delivered: string;
      people: readonly Person[];
      why: { title: string; quote: string };
    }
  | { kind: 'mail'; queue: string; to: string; subject: string; body: string };

export type Step = {
  n: '01' | '02' | '03' | '04';
  title: string;
  short: string;
  body: string;
  points: readonly [string, string, string];
  art: StepArt;
};

export type WorksLogo = 'gmail' | 'outlook' | 'linkedin' | 'excel' | 'api' | 'hubspot';

type Float = { key: string; value: string; source: string };

type Card = { title: string; body: string };

export type Section = 'how' | 'why' | 'openSource' | 'faq';

export type Content = {
  meta: { title: string; description: string };
  /** The id of each section in this language: the header links, and the language menu that keeps the section.
   *  The FAQ is `faq` in every language: the app's help link and old `/welcome#faq` links open it (`web/lib/landing.ts`). */
  sections: Record<Exclude<Section, 'faq'>, string> & { faq: 'faq' };
  nav: {
    homeLabel: string;
    label: string;
    how: string;
    why: string;
    openSource: string;
    faq: string;
    signIn: string;
    contact: string;
  };
  menu: {
    open: string;
    close: string;
    themeLabel: string;
    dark: string;
  };
  hero: {
    eyebrow: string;
    titleLine: string;
    titleEm: string;
    cta: string;
    seeHow: string;
    fine: string;
  };
  stage: {
    label: string;
    emailFloat: Float;
    refundFloat: Float;
    brief: string;
    planTitle: string;
    planBudget: string;
    columns: readonly [string, string, string];
    aiColumn: string;
    planFooter: string;
    rowsSummary: string;
  };
  ui: {
    verified: string;
    people: string;
    approveStart: string;
    toApprove: string;
    skip: string;
    approveSend: string;
    to: string;
    subject: string;
  };
  people: readonly Person[];
  works: { label: string; items: readonly { name: string; logo: WorksLogo; soon?: true }[] };
  statement: { line: string; em: string };
  how: {
    eyebrow: string;
    titleLine: string;
    titleRest: string;
    tablistLabel: string;
    steps: readonly Step[];
  };
  why: {
    eyebrow: string;
    titleLine: string;
    titleRest: string;
    source: Card & { cellValue: string; cellSource: string };
    approve: Card & { caption: string };
    replies: Card & { labels: readonly { label: string; tone?: 'pos' | 'info' | 'neg' }[] };
    market: Card;
  };
  openSource: {
    eyebrow: string;
    title: string;
    sub: string;
    selfHostTitle: string;
    selfHostBody: string;
    repoCta: string;
    hostedTitle: string;
    hostedBody: string;
    hostedCta: string;
  };
  faq: {
    eyebrow: string;
    title: string;
    moreBefore: string;
    moreAfter: string;
    items: readonly { q: string; a: string }[];
  };
  final: {
    title: string;
    lede: string;
    button: string;
  };
  footer: {
    legalLabel: string;
    terms: string;
    privacy: string;
    signIn: string;
    langLabel: string;
  };
};

export const content: Record<Locale, Content> = { it, en, es, fr };
