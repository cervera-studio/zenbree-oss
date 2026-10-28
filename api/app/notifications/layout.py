"""The service email layout (#221, handoff E1–E4): preheader, one title, one primary button, the reason you got it.

The auth emails (E1/E2) use the same layout from ``web/lib/email-layout.ts``: change both together. The
optional blocks (stats, boxes, notes, a secondary button: #351) are only the notification emails'.
Tables and inline styles for Outlook and Gmail; the ``prefers-color-scheme`` block covers the clients that
honour it, and the rest invert the light colours themselves.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.locale import Locale

MANAGE_PATH = '/settings/notifications'
FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
DARK = (
    '@media (prefers-color-scheme: dark){.zb-bg{background:#0f1b1b!important}'
    '.zb-card{background:#162424!important;border-color:#2a3b3b!important}'
    '.zb-tx{color:#e6efee!important}.zb-mu{color:#9fb3b1!important}.zb-ln{color:#7fd0dc!important}'
    '.zb-hr{border-color:#2a3b3b!important}.zb-tile{background:#1d2c2c!important}'
    '.zb-b2{border-color:#2a3b3b!important;color:#e6efee!important}}'
)
_COPY: dict[Locale, tuple[str, str]] = {
    'it': ('Se il pulsante non va, copia questo indirizzo nel browser:', 'Gestisci notifiche'),
    'en': ("If the button doesn't work, copy this address into your browser:", 'Manage notifications'),
    'es': ('Si el botón no funciona, copia esta dirección en tu navegador:', 'Gestionar notificaciones'),
    'fr': (
        'Si le bouton ne fonctionne pas, copiez cette adresse dans votre navigateur\u00a0:',
        'Gérer les notifications',
    ),
}


@dataclass(frozen=True)
class Email:
    subject: str
    text: str
    html: str


def render(
    *,
    subject: str,
    preheader: str,
    title: str,
    paragraphs: list[str],
    button: str,
    href: str,
    reason: str,
    manage: bool = False,
    stats: Sequence[tuple[str, str]] = (),
    boxes: Sequence[tuple[str, str, str]] = (),
    note: str = '',
    secondary: tuple[str, str] | None = None,
    fine_print: str = '',
    locale: Locale,
) -> Email:
    """Every argument is plain text and is escaped here. ``href`` is an in-app path; ``manage`` adds the
    link to the notification settings after the reason (the emails a user can turn off). ``stats``: (number,
    label) tiles; ``boxes``: (label, value, sub) tiles; ``note`` goes under them and ``fine_print`` under the
    buttons; ``secondary``: (label, in-app path) of a second button."""
    url = f'{settings.web_origin}{href}'
    fallback, manage_label = _COPY[locale]
    manage_url = f'{settings.web_origin}{MANAGE_PATH}'
    host = urlsplit(settings.web_origin).netloc or settings.web_origin
    p = 'margin:0 0 16px;font-size:16px;line-height:1.6;color:#10302f;'
    small = 'margin:0 0 8px;font-size:13px;line-height:1.5;color:#4f6664;'
    link = 'color:#0b6577;'
    body = ''.join(f'<p class="zb-tx" style="{p}">{escape(x)}</p>' for x in paragraphs)
    tile = 'padding:14px;border-radius:14px;background:#e8f0ef;vertical-align:top;'
    if stats:
        body += _tiles(
            f'<td class="zb-tile" width="{100 // len(stats) - 2}%" style="{tile}"><div class="zb-tx" style="font-size:30px;'
            f'line-height:1;font-family:Georgia,serif;color:#10302f;">{escape(n)}</div><div class="zb-mu" '
            f'style="margin-top:4px;font-size:12px;color:#4f6664;">{escape(label)}</div></td>'
            for n, label in stats
        )
    if boxes:
        body += _tiles(
            f'<td class="zb-tile" width="{100 // len(boxes) - 2}%" style="{tile}"><div class="zb-mu" style="font-size:12px;'
            f'font-weight:600;color:#4f6664;">{escape(label)}</div><div class="zb-tx" style="margin-top:4px;'
            f'font-size:15px;font-weight:700;color:#10302f;">{escape(value)}</div><div class="zb-mu" '
            f'style="font-size:12px;color:#4f6664;">{escape(sub)}</div></td>'
            for label, value, sub in boxes
        )
    if note:
        body += f'<p class="zb-mu" style="{small}margin-bottom:16px;">{escape(note)}</p>'
    second = ''
    if secondary:
        second = (
            f'<td width="10"></td><td class="zb-b2" style="border-radius:10px;border:1px solid #d5e0de;">'
            f'<a class="zb-tx" href="{escape(settings.web_origin + secondary[1])}" style="display:inline-block;'
            f'padding:12px 18px;font-family:{FONT};font-size:15px;font-weight:600;color:#10302f;'
            f'text-decoration:none;border-radius:10px;">{escape(secondary[0])}</a></td>'
        )
    manage_html = (
        f' <a class="zb-ln" href="{escape(manage_url)}" style="{link}font-weight:600;">{manage_label}</a>'
        if manage
        else ''
    )
    # Sent emails keep loading `/apple-icon.png`, the route of the web's `app/apple-icon.png`: keep that file name.
    html = f"""<!doctype html>
<html lang="{locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<title>{escape(subject)}</title><style>{DARK}</style></head>
<body class="zb-bg" style="margin:0;padding:0;background:#e8efee;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="zb-bg" style="background:#e8efee;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="zb-card"
 style="max-width:560px;background:#f7fbfa;border:1px solid #d5e0de;border-radius:16px;">
<tr><td style="padding:32px 36px;font-family:{FONT};">
<p class="zb-tx" style="margin:0 0 24px;font-size:17px;font-weight:700;color:#10302f;">\
<img src="{escape(settings.web_origin + '/apple-icon.png')}" width="24" height="24" alt="" \
style="display:inline-block;vertical-align:middle;border:0;border-radius:6px;">&nbsp;zenbree</p>
<h1 class="zb-tx" style="margin:0 0 16px;font-size:24px;line-height:1.3;color:#10302f;">{escape(title)}</h1>
{body}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 20px;"><tr>
<td style="border-radius:10px;background:#0b6577;"><a href="{escape(url)}" style="display:inline-block;\
padding:13px 22px;font-family:{FONT};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;\
border-radius:10px;">{escape(button)}</a></td>{second}</tr></table>
{f'<p class="zb-mu" style="{small}margin-bottom:16px;">{escape(fine_print)}</p>' if fine_print else ''}
<p class="zb-mu" style="{small}">{fallback}
<a class="zb-ln" href="{escape(url)}" style="{link}word-break:break-all;">{escape(url)}</a></p>
<hr class="zb-hr" style="border:0;border-top:1px solid #d5e0de;margin:24px 0 16px;">
<p class="zb-mu" style="{small}">{escape(reason)}{manage_html}</p>
<p class="zb-mu" style="margin:0;font-size:13px;color:#4f6664;">Zenbree · {escape(host)}</p>
</td></tr></table></td></tr></table></body></html>"""
    parts = [
        title,
        *paragraphs,
        *([' · '.join(f'{n} {label}' for n, label in stats)] if stats else []),
        *(f'{label}: {value}, {sub}' for label, value, sub in boxes),
        *([note] if note else []),
        f'{button}: {url}',
        *([f'{secondary[0]}: {settings.web_origin}{secondary[1]}'] if secondary else []),
        *([fine_print] if fine_print else []),
    ]
    manage_text = f'\n{manage_label}: {manage_url}' if manage else ''
    text = '\n\n'.join([*parts, f'--\n{reason}{manage_text}\nZenbree · {host}']) + '\n'
    return Email(subject, text, html)


def _tiles(cells) -> str:
    """One row of tiles with a gap: a table, which every client lays out, unlike a grid."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;'
        f'border-collapse:separate;border-spacing:0;"><tr>{'<td width="3%"></td>'.join(cells)}</tr></table>'
    )
