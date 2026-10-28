import type { Locale } from '@/i18n/config';
import { logger } from '@/lib/logger';
import { COPY, FALLBACK } from './email-copy';

// The service email layout (#221, handoff E1–E4): preheader, one title, one primary button, the reason
// you got it. Mirror of `api/app/notifications/layout.py`, which renders the notification emails: change
// both together. Tables and inline styles for Outlook and Gmail; the dark block covers the clients that
// honour `prefers-color-scheme`, the rest invert the light colours themselves.

const FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif";
const DARK =
  '@media (prefers-color-scheme: dark){.zb-bg{background:#0f1b1b!important}' +
  '.zb-card{background:#162424!important;border-color:#2a3b3b!important}' +
  '.zb-tx{color:#e6efee!important}.zb-mu{color:#9fb3b1!important}.zb-ln{color:#7fd0dc!important}' +
  '.zb-hr{border-color:#2a3b3b!important}}';

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

interface Email {
  subject: string;
  text: string;
  html: string;
}

/** Every field is plain text and is escaped here; `url` is absolute. */
function renderEmail(e: {
  locale: Locale;
  subject: string;
  preheader: string;
  title: string;
  paragraphs: string[];
  button: string;
  url: string;
  reason: string;
}): Email {
  const host = new URL(e.url).host;
  const p = 'margin:0 0 16px;font-size:16px;line-height:1.6;color:#10302f;';
  const small = 'margin:0 0 8px;font-size:13px;line-height:1.5;color:#4f6664;';
  const url = esc(e.url);
  const body = e.paragraphs.map((x) => `<p class="zb-tx" style="${p}">${esc(x)}</p>`).join('');
  // Sent emails keep loading `/apple-icon.png`, the route of `app/apple-icon.png`: keep that file name.
  const html = `<!doctype html>
<html lang="${e.locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<title>${esc(e.subject)}</title><style>${DARK}</style></head>
<body class="zb-bg" style="margin:0;padding:0;background:#e8efee;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">${esc(e.preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="zb-bg" style="background:#e8efee;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="zb-card"
 style="max-width:560px;background:#f7fbfa;border:1px solid #d5e0de;border-radius:16px;">
<tr><td style="padding:32px 36px;font-family:${FONT};">
<p class="zb-tx" style="margin:0 0 24px;font-size:17px;font-weight:700;color:#10302f;"><img src="${esc(`${new URL(e.url).origin}/apple-icon.png`)}" width="24" height="24" alt="" style="display:inline-block;vertical-align:middle;border:0;border-radius:6px;">&nbsp;zenbree</p>
<h1 class="zb-tx" style="margin:0 0 16px;font-size:24px;line-height:1.3;color:#10302f;">${esc(e.title)}</h1>
${body}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 20px;"><tr>
<td style="border-radius:10px;background:#0b6577;"><a href="${url}" style="display:inline-block;padding:13px 22px;font-family:${FONT};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px;">${esc(e.button)}</a></td></tr></table>
<p class="zb-mu" style="${small}">${esc(FALLBACK[e.locale])}
<a class="zb-ln" href="${url}" style="color:#0b6577;word-break:break-all;">${url}</a></p>
<hr class="zb-hr" style="border:0;border-top:1px solid #d5e0de;margin:24px 0 16px;">
<p class="zb-mu" style="${small}">${esc(e.reason)}</p>
<p class="zb-mu" style="margin:0;font-size:13px;color:#4f6664;">Zenbree · ${esc(host)}</p>
</td></tr></table></td></tr></table></body></html>`;
  const text = `${[e.title, ...e.paragraphs, `${e.button}: ${e.url}`, `--\n${e.reason}\nZenbree · ${host}`].join('\n\n')}\n`;
  return { subject: e.subject, text, html };
}

/** E1/E2, sent by `sendAuthEmail`. */
function authEmail(
  template: 'verify-email' | 'reset-password' | 'change-email',
  to: string,
  name: string,
  url: string,
  locale: Locale,
  newEmail = '',
) {
  const copy = COPY[locale];
  // The sign-up form's name, unchecked: capped like the API's stranger names, and left out when it looks
  // like an address, which mail clients would turn into a link even as escaped text.
  const first = (name.trim().split(/\s+/)[0] ?? '').slice(0, 60);
  const safeFirst = first && !/[./:@]/.test(first) ? first : undefined;
  if (template === 'verify-email') {
    const c = copy.verify;
    return renderEmail({
      locale,
      subject: c.subject,
      preheader: c.preheader,
      title: c.title,
      paragraphs: [c.intro(safeFirst, to), c.note],
      button: c.button,
      url,
      reason: copy.securityReason,
    });
  }
  if (template === 'change-email') {
    const c = copy.changeEmail;
    return renderEmail({
      locale,
      subject: c.subject,
      preheader: c.preheader,
      title: c.title,
      paragraphs: c.paragraphs(to, newEmail),
      button: c.button,
      url,
      reason: copy.securityReason,
    });
  }
  const c = copy.reset;
  return renderEmail({
    locale,
    subject: c.subject,
    preheader: c.preheader,
    title: c.title,
    paragraphs: c.paragraphs(to),
    button: c.button,
    url,
    reason: copy.securityReason,
  });
}

/** The invitation to a team (#308), sent by Better Auth's `sendInvitationEmail`. */
function inviteEmail(inviter: string, org: string, url: string, locale: Locale) {
  const c = COPY[locale].invite;
  // The inviter's address is verified; the org name is whatever an admin typed, so it stays out of the
  // subject, is capped, and is left out when it looks like an address or a link, like the greeting name.
  const name = org.trim().slice(0, 60);
  const team = name && !/[./:@]/.test(name) ? c.team(name) : c.anonymousTeam;
  return renderEmail({
    locale,
    subject: c.subject,
    preheader: c.preheader(team),
    title: c.title(team),
    paragraphs: c.paragraphs(inviter, team),
    button: c.button,
    url,
    reason: c.reason,
  });
}

// Resend REST, no SDK, like the API's notifications (#221: Better Auth Infra templates take variables,
// not our HTML). Not awaited by Better Auth's callbacks; no key or sender = inert, as in the API.
function send(template: string, to: string, { subject, text, html }: Email) {
  const key = process.env.RESEND_API_KEY;
  const from = process.env.EMAIL_FROM;
  if (!(key && from)) {
    logger.error('auth.email.failed', { template, error: 'RESEND_API_KEY or EMAIL_FROM not set' });
    return;
  }
  void fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ from, to: [to], subject, text, html }),
    signal: AbortSignal.timeout(10_000), // as the API's send_email
  })
    // No recipient, link or key in the log: the template and Resend's status are enough to act on.
    .then((res) => {
      if (!res.ok) logger.error('auth.email.failed', { template, status: res.status });
    })
    .catch((error: unknown) =>
      logger.error('auth.email.failed', {
        template,
        status: null,
        error: error instanceof Error ? error.name : 'Unknown email service error',
      }),
    );
}

export async function sendAuthEmail(
  template: 'verify-email' | 'reset-password' | 'change-email',
  to: string,
  name: string,
  url: string,
  locale: Locale,
  newEmail?: string,
) {
  send(template, to, authEmail(template, to, name, url, locale, newEmail));
}

export async function sendInviteEmail(
  to: string,
  inviter: string,
  org: string,
  url: string,
  locale: Locale,
) {
  send('invite', to, inviteEmail(inviter, org, url, locale));
}
