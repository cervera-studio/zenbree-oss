"""The service emails in Spanish."""

from __future__ import annotations

from datetime import datetime

from app.core.locale import Locale
from app.notifications import layout
from app.notifications.emails.facts import (
    LOW_CREDITS,
    Digest,
    LowCredits,
    SearchDone,
    Words,
    contact,
    digest_head,
    digest_lines,
    euros,
    grouped,
    one_line,
    pl,
)

LOCALE: Locale = 'es'
ASKED = 'Recibes este email porque activaste «{}» en tus notificaciones.'
LABELS = {'interested': 'interesado', 'meeting_request': 'pide una reunión'}
MONTHS = ('ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sept', 'oct', 'nov', 'dic')
WEEKDAYS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
APPROVALS: Words = ('mensaje por aprobar', 'mensajes por aprobar')
DIGEST_LINES: dict[str, Words] = {
    'reply_urgent': ('respuesta interesada o petición de reunión', 'respuestas interesadas o peticiones de reunión'),
    'reply': ('respuesta más a tus campañas', 'respuestas más a tus campañas'),
    'linkedin_accepted': ('invitación de LinkedIn aceptada', 'invitaciones de LinkedIn aceptadas'),
    'search_done': ('búsqueda terminada', 'búsquedas terminadas'),
    'search_failed': ('búsqueda detenida', 'búsquedas detenidas'),
}
SUBJECT_WORDS: dict[str, Words] = {
    'reply': ('respuesta', 'respuestas'),
    'linkedin_accepted': ('invitación aceptada', 'invitaciones aceptadas'),
    'search_done': DIGEST_LINES['search_done'],
    'search_failed': DIGEST_LINES['search_failed'],
}


def _num(n: int) -> str:
    return grouped(n, '.')


def _day(at: datetime) -> str:
    return f'{at.day} {MONTHS[at.month - 1]}'


def reply_urgent(href: str, data: dict) -> layout.Email:
    who = contact(data, 'Un contacto')
    label = LABELS.get(data.get('label', ''), data.get('label', ''))
    campaign = one_line(data.get('campaign'))
    where = f'a la campaña «{campaign}»' if campaign else 'a tu campaña'
    return layout.render(
        subject='Nueva respuesta interesada en Zenbree',
        preheader=f'Etiqueta: {label}. La respuesta está en Zenbree.',
        title='Tienes una respuesta interesada',
        paragraphs=[
            f'{who} ha respondido {where} por {data.get("channel") or "email"}. Etiqueta: {label}.',
            'El texto de la respuesta se queda en Zenbree.',
        ],
        button='Abrir y responder',
        href=href,
        secondary=('Ver el borrador', f'{href}#reply'),
        reason=ASKED.format('Respuesta interesada o petición de reunión'),
        manage=True,
        locale=LOCALE,
    )


def search_failed(href: str, data: dict) -> layout.Email:
    chat = one_line(data.get('chat'))
    done, units = data.get('done'), data.get('units')
    where = f'La búsqueda «{chat}»' if chat else 'Tu búsqueda'
    progress = f' tras {done} de {units}' if units else ''
    return layout.render(
        subject='Búsqueda detenida en Zenbree',
        preheader='Un proveedor no responde. Lo que falta no lo pagas.',
        title='Tu búsqueda se ha detenido',
        paragraphs=[
            f'{where} se ha detenido{progress}: un proveedor no responde.',
            'Lo que falta no se te ha cobrado. Reinténtalo desde el chat cuando quieras.',
        ],
        button='Abrir el chat',
        href=href,
        reason=ASKED.format('Búsqueda detenida o hace falta tu decisión'),
        manage=True,
        locale=LOCALE,
    )


def bounces_high(href: str, data: dict) -> layout.Email:
    campaign = one_line(data.get('campaign'))
    invalid, attempted = data.get('invalid'), data.get('attempted')
    what = f'La campaña «{campaign}»' if campaign else 'Una de tus campañas'
    return layout.render(
        subject=f'Demasiados rebotes en la campaña «{campaign}»' if campaign else 'Demasiados rebotes en una campaña',
        preheader='Muchas direcciones no son válidas. Revisa la lista antes de continuar.',
        title='Demasiadas direcciones no válidas',
        paragraphs=[
            f'{what} ha tenido {invalid} {pl(invalid or 0, "email rebotado", "emails rebotados")} de {attempted} '
            f'{pl(attempted or 0, "enviado", "enviados")}: la hemos pausado.'
            if data.get('paused')
            else f'{what} tiene {invalid} {pl(invalid or 0, "dirección no válida", "direcciones no válidas")} de '
            f'{attempted} {pl(attempted or 0, "persona a la que", "personas a las que")} escribir.',
            'Una tasa alta de rebotes daña la reputación de tus buzones. '
            'Revisa las direcciones de la lista antes de continuar.',
        ],
        button='Abrir la campaña',
        href=href,
        reason='Recibes este email porque afecta a la reputación de tus buzones. No se puede desactivar.',
        manage=True,
        locale=LOCALE,
    )


def mailbox_error(href: str, data: dict) -> layout.Email:
    mailbox = one_line(data.get('mailbox'))
    linkedin = data.get('provider') == 'unipile'
    account = f'«{mailbox}»' if mailbox else 'Tu cuenta de LinkedIn' if linkedin else 'Uno de tus buzones'
    return layout.render(
        subject='Cuenta de LinkedIn desconectada en Zenbree' if linkedin else 'Buzón desconectado en Zenbree',
        preheader='Las campañas de LinkedIn no envían hasta que la reconectes.'
        if linkedin
        else 'Las campañas que lo usan no envían hasta que lo reconectes.',
        title='Una cuenta de LinkedIn ya no está conectada' if linkedin else 'Un buzón ya no está conectado',
        paragraphs=[
            f'{account} ya no responde a Zenbree.',
            'Hasta que la reconectes, las campañas de LinkedIn no envían invitaciones ni mensajes.'
            if linkedin
            else 'Hasta que lo reconectes, las campañas que lo usan no envían nada desde este buzón.',
        ],
        button='Reconectar la cuenta' if linkedin else 'Reconectar el buzón',
        href=href,
        reason='Recibes este email porque afecta a tus envíos. No se puede desactivar.',
        manage=True,
        locale=LOCALE,
    )


def payment_failed(href: str, data: dict) -> layout.Email:
    # #323: an admin of several organizations must know which one.
    org = one_line(data.get('organization'))
    return layout.render(
        subject='La recarga automática ha fallado',
        preheader='La recarga automática se ha desactivado.',
        title='La recarga automática ha fallado',
        paragraphs=[
            f'La recarga automática de créditos{f" de «{org}»" if org else ""} ha fallado y se ha desactivado.',
            'Revisa tu método de pago y vuelve a activarla.',
        ],
        button='Revisar el pago',
        href=href,
        reason='Recibes este email porque la recarga automática está activada en tu cuenta. '
        'No se puede desactivar: se trata de pagos.',
        locale=LOCALE,
    )


def search_done(f: SearchDone) -> layout.Email:
    stats = [(_num(f.people), pl(f.people, 'persona', 'personas'))] * bool(f.people)
    stats += [(_num(f.companies), pl(f.companies, 'empresa', 'empresas'))] * bool(f.companies)
    found = ', '.join(f'{n} {label}' for n, label in stats)
    spent = pl(f.spent, 'crédito gastado', 'créditos gastados')
    if f.refunded:
        spent += f' · {_num(f.refunded)} {pl(f.refunded, "devuelto", "devueltos")}'
    stats.append((_num(f.spent), spent))
    back = f' {_num(f.refunded)} {pl(f.refunded, "crédito devuelto", "créditos devueltos")}.' if f.refunded else ''
    note = (
        f'{_num(f.undelivered)} {pl(f.undelivered, "fila no entregada", "filas no entregadas")}: no las pagas. '
        if f.undelivered
        else ''
    ) + (f'Saldo: {_num(f.balance)} {pl(f.balance, "crédito", "créditos")}.' if f.balance is not None else '')
    return layout.render(
        subject=f'Tu lista «{f.name}» está lista' if f.name else 'Tu búsqueda en Zenbree está lista',
        preheader=f'{found}.{back}' if found else 'Los resultados te esperan en el chat.',
        title='Tu lista está lista',
        paragraphs=[
            f'El agente ha terminado la búsqueda «{f.chat}».' if f.chat else 'El agente ha terminado la búsqueda.'
        ],
        stats=stats,
        note=note.strip(),
        button='Abrir la lista',
        href=f.href,
        secondary=('Preparar la campaña', f.campaign_href) if f.campaign_href else None,
        reason=ASKED.format('Búsqueda terminada'),
        manage=True,
        locale=LOCALE,
    )


def low_credits(f: LowCredits) -> layout.Email:
    boxes = []
    if f.phones:  # a box that says «o 0 teléfonos» reads like a bug: under one phone's price, none
        boxes.append(
            (
                f'Con {_num(f.credits)} créditos',
                f'~{_num(f.emails)} emails encontrados',
                f'o {_num(f.phones)} {pl(f.phones, "teléfono", "teléfonos")}',
            )
        )
    when = ''
    if r := f.renewal:
        when = _day(r.at)
        soon = {0: 'hoy', 1: 'mañana'}.get(r.days, f'dentro de {r.days} días')
        # Not "+N": a period's allowance replaces the unused one (billing.routes), it does not add to it.
        boxes.append((f'Plan {r.plan}', f'{_num(r.credits)} créditos el {when}', soon))
    packs = ' · '.join(
        f'{_num(credits)}{" créditos" if i == 0 else ""} {euros(cents, ".", ",", symbol_after=True)}'
        for i, (credits, cents) in enumerate(f.packs)
    )
    left = pl(f.credits, 'queda 1 crédito', f'quedan {_num(f.credits)} créditos')
    title = f'«{f.org}»: {left}' if f.org else f'Te {left}'
    preheader = (
        f'Alcanzan para unos {_num(f.emails)} emails encontrados.'
        if f.emails
        else 'Recarga para no detener las búsquedas.'
    ) + (f' El plan se renueva el {when}.' if when else '')
    return layout.render(
        subject=title,
        preheader=preheader,
        title=title,
        paragraphs=[
            f'{f"«{f.org}» está" if f.org else "Estás"} por debajo del umbral de {LOW_CREDITS} créditos. Las búsquedas'
            ' en curso terminan igualmente; las nuevas se detienen en el plan si los créditos no alcanzan.'
        ],
        boxes=boxes,
        button='Recargar créditos',
        href=f.href,
        secondary=(f'Pasar a {f.upgrade}', f.href) if f.upgrade else None,
        fine_print=f'Recargas: {packs}. Los créditos recargados no caducan.' if packs else '',
        # #343: an always email, so it never claims the user turned it on.
        reason='Recibes este email porque afecta a los créditos de tu organización. No se puede desactivar.',
        manage=True,
        locale=LOCALE,
    )


def digest_subject(d: Digest) -> str:
    head = digest_head(d, APPROVALS, SUBJECT_WORDS, ' y ')
    return f'{head} · {WEEKDAYS[d.at.weekday()]} {_day(d.at)}'


def digest(d: Digest) -> layout.Email:
    """Counts only: no names, no reply text."""
    waiting = digest_lines(d, APPROVALS, DIGEST_LINES)
    credits = [
        f'Créditos disponibles: {credits}' if len(d.balances) == 1 else f'Créditos de «{org}»: {credits}'
        for org, credits in d.balances
    ]
    return layout.render(
        subject=digest_subject(d),
        preheader=', '.join(waiting),
        title=f'Hola, {d.first_name}' if d.first_name else 'Tu resumen',
        paragraphs=[*waiting, *credits],
        button='Abrir Zenbree',
        href='/',
        reason=f'Recibes este resumen {"cada lunes" if d.weekly else "los días laborables"}'
        ' porque activaste «Resumen por email».',
        manage=True,
        locale=LOCALE,
    )


def erasure_request(email: str) -> tuple[str, str]:
    """(subject, text) to the sender whose recipient asked, from the unsubscribe page, to be deleted."""
    return (
        'Un destinatario ha pedido que se eliminen sus datos',
        f'{email} ha pedido que se eliminen sus datos personales (art. 17 del RGPD) desde la página de baja de '
        'tus campañas de Zenbree.\n'
        'Ya está en tus exclusiones y no recibirá más mensajes. Elimina sus datos de tus listas y de tus '
        'sistemas, y respóndele en el plazo de un mes.\n',
    )
