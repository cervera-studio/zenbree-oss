import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { sendAuthEmail, sendInviteEmail } from './email-layout';
import { logger } from './logger';

const url = 'https://app.zenbree.test/api/auth/verify-email?token=secret-token&callbackURL=%2F';

type Sent = { from: string; to: string[]; subject: string; text: string; html: string };

function stubResend(status: number) {
  const fetchMock = vi.fn(
    async (_url: string, _init: RequestInit) => new Response('{}', { status }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return (i = 0) => JSON.parse(String(fetchMock.mock.calls[i]?.[1].body)) as Sent;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('sendAuthEmail', () => {
  let logged: unknown[][];

  beforeEach(() => {
    vi.stubEnv('RESEND_API_KEY', 're_test');
    vi.stubEnv('EMAIL_FROM', 'Zenbree <noreply@zenbree.test>');
    logged = [];
    vi.spyOn(logger, 'error').mockImplementation((...args) => {
      logged.push(args);
    });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('sends the verify email in the layout, escaped, with a text part', async () => {
    const sent = stubResend(200);
    await sendAuthEmail('verify-email', 'ale@x.test', '<b>Ale Rossi', url, 'it');
    const mail = sent();
    expect(mail.to).toEqual(['ale@x.test']);
    expect(mail.subject).toBe('Verifica la tua email');
    expect(mail.html).not.toContain('<b>Ale');
    expect(mail.html).toContain('Ciao &#60;b&#62;Ale,');
    expect(mail.html).toContain('token=secret-token&#38;callbackURL');
    expect(mail.html.match(/<h1/g)).toHaveLength(1);
    expect(mail.html).toContain('prefers-color-scheme: dark');
    expect(mail.html).toContain('<img src="https://app.zenbree.test/apple-icon.png"');
    expect(mail.text).toMatch(/^Conferma il tuo indirizzo email\n/);
    expect(mail.text).toContain(`Verifica email: ${url}`);
    expect(mail.text).toContain(
      'Non possiamo disattivarla: riguarda la sicurezza.\nZenbree · app.zenbree.test',
    );
    expect(logged).toEqual([]);
  });

  it('change-email goes to the current address and names the new one', async () => {
    const sent = stubResend(200);
    await sendAuthEmail('change-email', 'ale@x.test', '', url, 'en', 'ale@new.test');
    expect(sent().to).toEqual(['ale@x.test']);
    expect(sent().text).toContain('change the email of ale@x.test to ale@new.test');
  });

  it('caps the greeting name and leaves out one that looks like a link', async () => {
    const sent = stubResend(200);
    await sendAuthEmail('verify-email', 'ale@x.test', 'evil.example.com/login', url, 'it');
    await sendAuthEmail('verify-email', 'ale@x.test', 'A'.repeat(500), url, 'it');
    const [link, long] = [0, 1].map((i) => sent(i).text);
    expect(link).not.toContain('evil');
    expect(link).toContain('\n\nManca un passaggio');
    expect(long).toContain(`Ciao ${'A'.repeat(60)}, `);
    expect(long).not.toContain('A'.repeat(61));
  });

  it('logs a rejected send with its status and no recipient, link or key', async () => {
    stubResend(403);
    await sendAuthEmail('reset-password', 'ale@x.test', 'Ale', url, 'it');
    await flush();
    expect(logged).toEqual([['auth.email.failed', { template: 'reset-password', status: 403 }]]);
    expect(JSON.stringify(logged)).not.toMatch(/ale@x\.test|secret-token|re_test/);
  });

  it('sends nothing without a key', async () => {
    vi.stubEnv('RESEND_API_KEY', '');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await sendAuthEmail('verify-email', 'ale@x.test', 'Ale', url, 'it');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(logged[0]?.[0]).toBe('auth.email.failed');
  });
});

describe('sendInviteEmail', () => {
  beforeEach(() => {
    vi.stubEnv('RESEND_API_KEY', 're_test');
    vi.stubEnv('EMAIL_FROM', 'Zenbree <noreply@zenbree.test>');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('names the inviter and the team, escaped and capped, and links the invitation', async () => {
    const sent = stubResend(200);
    const invite = 'https://app.zenbree.test/invite/inv-1';
    await sendInviteEmail('bo@x.test', 'ada@acme.test', `<b>Acme ${'x'.repeat(80)}`, invite, 'it');
    const mail = sent();
    expect(mail.to).toEqual(['bo@x.test']);
    expect(mail.subject).not.toMatch(/Acme/);
    expect(mail.html).toContain('ada@acme.test');
    expect(mail.html).toContain('&#60;b&#62;Acme');
    expect(mail.html).not.toContain('x'.repeat(60));
    expect(mail.text).toContain(`Apri l'invito: ${invite}`);
  });

  it.each(['evil.com', 'Paga su http://x', 'ceo@acme.test'])(
    'leaves out an org name that mail clients would turn into a link: %s',
    async (org) => {
      const sent = stubResend(200);
      await sendInviteEmail(
        'bo@x.test',
        'ada@acme.test',
        org,
        'https://app.zenbree.test/invite/i',
        'it',
      );
      const mail = sent();
      expect(mail.html).not.toContain(org);
      expect(mail.text).not.toContain(org);
      expect(mail.text).toContain('ada@acme.test ti ha invitato in un team su Zenbree.');
    },
  );
});

describe('service emails by locale', () => {
  beforeEach(() => {
    vi.stubEnv('RESEND_API_KEY', 're_test');
    vi.stubEnv('EMAIL_FROM', 'Zenbree <noreply@zenbree.test>');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('sends the invitation in English, leaving out a team name that looks like a link', async () => {
    const sent = stubResend(200);
    const invite = 'https://app.zenbree.test/invite/inv-1';
    await sendInviteEmail('bo@x.test', 'ada@acme.test', 'Acme', invite, 'en');
    await sendInviteEmail('bo@x.test', 'ada@acme.test', 'evil.com', invite, 'en');
    const [named, anonymous] = [sent(0), sent(1)];
    expect(named.subject).toBe("You've been invited to a team on Zenbree");
    expect(named.html).toContain('<html lang="en">');
    expect(named.text).toContain('ada@acme.test invited you to the Acme team on Zenbree.');
    expect(named.text).toContain(`Open the invitation: ${invite}`);
    expect(anonymous.text).toContain('invited you to a team on Zenbree.');
    expect(anonymous.text).not.toContain('evil');
  });
});
