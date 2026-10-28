import type { Locale } from '../content';

/** Endonyms, the same on every page. A flag is SVG markup for a 4:3 box: emoji flags do not render on Windows. */
export const languages: Record<Locale, { name: string; flag: string }> = {
  it: {
    name: 'Italiano',
    flag: '<svg viewBox="0 0 3 2" preserveAspectRatio="xMidYMid slice"><path fill="#009246" d="M0 0h1v2H0z"/><path fill="#fff" d="M1 0h1v2H1z"/><path fill="#ce2b37" d="M2 0h1v2H2z"/></svg>',
  },
  en: {
    name: 'English',
    flag: '<svg viewBox="0 0 60 30" preserveAspectRatio="xMidYMid slice"><path fill="#012169" d="M0 0h60v30H0z"/><path stroke="#fff" stroke-width="6" d="M0 0l60 30M60 0L0 30"/><path stroke="#c8102e" stroke-width="2" d="M0 0l60 30M60 0L0 30"/><path stroke="#fff" stroke-width="10" d="M30 0v30M0 15h60"/><path stroke="#c8102e" stroke-width="6" d="M30 0v30M0 15h60"/></svg>',
  },
  es: {
    name: 'Español',
    flag: '<svg viewBox="0 0 3 2" preserveAspectRatio="xMidYMid slice"><path fill="#aa151b" d="M0 0h3v2H0z"/><path fill="#f1bf00" d="M0 .5h3v1H0z"/></svg>',
  },
  fr: {
    name: 'Français',
    flag: '<svg viewBox="0 0 3 2" preserveAspectRatio="xMidYMid slice"><path fill="#002654" d="M0 0h1v2H0z"/><path fill="#fff" d="M1 0h1v2H1z"/><path fill="#ce1126" d="M2 0h1v2H2z"/></svg>',
  },
};
