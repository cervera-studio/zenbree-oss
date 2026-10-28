import { describe, expect, it } from 'vite-plus/test';
import { LOCALES, type Locale } from '../i18n/config';
import { MESSAGES } from './index';

/**
 * The term base: one word for each idea, in every language. A second name for the same thing
 * (a chat that the nav calls a search, a client that one page calls a project) makes the user
 * learn it twice, so each synonym we replaced is banned from every catalog.
 */
const TERMS: Record<Locale, { chats: string; newChat: string; banned: RegExp[] }> = {
  en: {
    chats: 'Chats',
    newChat: 'New chat',
    banned: [/\bnew search\b/i, /\bagent chats\b/i, /\bprojects?\b/i, /\bexport csv\b/i],
  },
  it: {
    chats: 'Chat',
    newChat: 'Nuova chat',
    banned: [
      /\bnuova ricerca\b/i,
      /\bchat con l['’]agente\b/i,
      /\bprogett[oi]\b/i,
      /\besporta csv\b/i,
    ],
  },
  es: {
    chats: 'Chats',
    newChat: 'Nuevo chat',
    banned: [
      /\bnueva búsqueda\b/i,
      /\bchats con el agente\b/i,
      /\bproyectos?\b/i,
      /\bexportar csv\b/i,
    ],
  },
  fr: {
    chats: 'Conversations',
    newChat: 'Nouvelle conversation',
    banned: [
      /\bnouvelle recherche\b/i,
      /\bconversations avec l['’]agent\b/i,
      /\bprojets?\b/i,
      /\bexporter en csv\b/i,
    ],
  },
};

function values(obj: object, prefix = ''): [string, string][] {
  return Object.entries(obj).flatMap(([k, v]): [string, string][] =>
    typeof v === 'string' ? [[`${prefix}${k}`, v]] : values(v, `${prefix}${k}.`),
  );
}

describe.each(LOCALES)('%s term base', (locale) => {
  const messages = MESSAGES[locale];
  const terms = TERMS[locale];

  it('names the chats and the new-chat action with one word each', () => {
    expect([
      messages.nav.searches,
      messages.chat.newChat,
      messages.lists.newChat,
      messages.campaigns.new.newChat,
    ]).toEqual([terms.chats, terms.newChat, terms.newChat, terms.newChat]);
  });

  it('names why Instantly leaves a person out with the same words in the list dialog and the run', () => {
    const list = messages.lists.instantly.reason;
    const run = messages.automations.instantlySkipped;
    expect([list.stopped, list.crm_hold]).toEqual([run.stopped, run.crmHold]);
  });

  it('uses no banned synonym', () => {
    const hits = values(messages).filter(([, text]) => terms.banned.some((re) => re.test(text)));
    expect(hits).toEqual([]);
  });
});
