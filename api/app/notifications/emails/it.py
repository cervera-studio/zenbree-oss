"""The service emails in Italian."""

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

LOCALE: Locale = 'it'
ASKED = 'Ricevi questa email perché hai attivato «{}» nelle notifiche.'
LABELS = {'interested': 'interessato', 'meeting_request': 'vuole fissare un incontro'}
MONTHS = ('gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic')
WEEKDAYS = ('lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica')
APPROVALS: Words = ('messaggio da approvare', 'messaggi da approvare')
DIGEST_LINES: dict[str, Words] = {
    'reply_urgent': ('risposta interessata o richiesta di incontro', 'risposte interessate o richieste di incontro'),
    'reply': ('altra risposta alle tue campagne', 'altre risposte alle tue campagne'),
    'linkedin_accepted': ('invito LinkedIn accettato', 'inviti LinkedIn accettati'),
    'search_done': ('ricerca finita', 'ricerche finite'),
    'search_failed': ('ricerca ferma', 'ricerche ferme'),
}
SUBJECT_WORDS: dict[str, Words] = {
    'reply': ('risposta', 'risposte'),
    'linkedin_accepted': ('invito accettato', 'inviti accettati'),
    'search_done': DIGEST_LINES['search_done'],
    'search_failed': DIGEST_LINES['search_failed'],
}


def _num(n: int) -> str:
    return grouped(n, '.')


def _day(at: datetime) -> str:
    return f'{at.day} {MONTHS[at.month - 1]}'


def reply_urgent(href: str, data: dict) -> layout.Email:
    who = contact(data, 'Un contatto')
    label = LABELS.get(data.get('label', ''), data.get('label', ''))
    campaign = one_line(data.get('campaign'))
    where = f'alla campagna «{campaign}»' if campaign else 'alla tua campagna'
    return layout.render(
        subject='Nuova risposta interessata su Zenbree',
        preheader=f'Etichetta: {label}. La risposta è in Zenbree.',
        title='Hai una risposta interessata',
        paragraphs=[
            f'{who} ha risposto {where} via {data.get("channel") or "email"}. Etichetta: {label}.',
            'Il testo della risposta resta in Zenbree.',
        ],
        button='Apri e rispondi',
        href=href,
        secondary=('Vedi la bozza', f'{href}#reply'),
        reason=ASKED.format('Risposta interessata o richiesta di incontro'),
        manage=True,
        locale=LOCALE,
    )


def search_failed(href: str, data: dict) -> layout.Email:
    chat = one_line(data.get('chat'))
    done, units = data.get('done'), data.get('units')
    where = f'La ricerca «{chat}»' if chat else 'La tua ricerca'
    progress = f' dopo {done} di {units}' if units else ''
    return layout.render(
        subject='Ricerca ferma su Zenbree',
        preheader='Un fornitore non risponde. Quello che manca non lo paghi.',
        title='La tua ricerca si è fermata',
        paragraphs=[
            f'{where} si è fermata{progress}: un fornitore non risponde.',
            'Quello che manca non ti è stato addebitato. Riprova dalla chat quando vuoi.',
        ],
        button='Apri la chat',
        href=href,
        reason=ASKED.format('Ricerca ferma o serve una tua scelta'),
        manage=True,
        locale=LOCALE,
    )


def bounces_high(href: str, data: dict) -> layout.Email:
    campaign = one_line(data.get('campaign'))
    invalid, attempted = data.get('invalid'), data.get('attempted')
    what = f'La campagna «{campaign}»' if campaign else 'Una tua campagna'
    return layout.render(
        subject=f'Troppi rimbalzi nella campagna «{campaign}»' if campaign else 'Troppi rimbalzi in una campagna',
        preheader='Molti indirizzi non sono validi. Controlla la lista prima di continuare.',
        title='Troppi indirizzi non validi',
        paragraphs=[
            f'{what} ha avuto {invalid} email rimbalzate su {attempted} inviate: l’abbiamo messa in pausa.'
            if data.get('paused')
            else f'{what} ha {invalid} indirizzi non validi su {attempted} persone a cui scrivere.',
            'Un alto tasso di rimbalzi danneggia la reputazione delle tue caselle. '
            'Controlla gli indirizzi nella lista prima di continuare.',
        ],
        button='Apri la campagna',
        href=href,
        reason='Ricevi questa email perché riguarda la reputazione delle tue caselle. Non possiamo disattivarla.',
        manage=True,
        locale=LOCALE,
    )


def mailbox_error(href: str, data: dict) -> layout.Email:
    mailbox = one_line(data.get('mailbox'))
    linkedin = data.get('provider') == 'unipile'
    account = f'«{mailbox}»' if mailbox else 'Il tuo account LinkedIn' if linkedin else 'Una tua casella'
    return layout.render(
        subject='Account LinkedIn scollegato su Zenbree' if linkedin else 'Casella scollegata su Zenbree',
        preheader='Le campagne LinkedIn non inviano finché non lo ricolleghi.'
        if linkedin
        else 'Le campagne che la usano non inviano finché non la ricolleghi.',
        title='Un account LinkedIn non è più collegato' if linkedin else 'Una casella non è più collegata',
        paragraphs=[
            f'{account} non risponde più a Zenbree.',
            'Finché non lo ricolleghi, le campagne LinkedIn non inviano inviti né messaggi.'
            if linkedin
            else 'Finché non la ricolleghi, le campagne che la usano non inviano da questa casella.',
        ],
        button='Ricollega l’account' if linkedin else 'Ricollega la casella',
        href=href,
        reason='Ricevi questa email perché riguarda i tuoi invii. Non possiamo disattivarla.',
        manage=True,
        locale=LOCALE,
    )


def payment_failed(href: str, data: dict) -> layout.Email:
    # #323: an admin of several organizations must know which one.
    org = one_line(data.get('organization'))
    return layout.render(
        subject='Ricarica automatica non riuscita',
        preheader='La ricarica automatica è stata disattivata.',
        title='Ricarica automatica non riuscita',
        paragraphs=[
            f'La ricarica automatica dei crediti{f" di «{org}»" if org else ""} non è andata a buon fine'
            ' ed è stata disattivata.',
            'Controlla il metodo di pagamento e riattivala.',
        ],
        button='Controlla il pagamento',
        href=href,
        reason='Ricevi questa email perché la ricarica automatica è attiva sul tuo account. '
        'Non possiamo disattivarla: riguarda i pagamenti.',
        locale=LOCALE,
    )


def search_done(f: SearchDone) -> layout.Email:
    stats = [(_num(f.people), pl(f.people, 'persona', 'persone'))] * bool(f.people)
    stats += [(_num(f.companies), pl(f.companies, 'azienda', 'aziende'))] * bool(f.companies)
    found = ', '.join(f'{n} {label}' for n, label in stats)
    spent = pl(f.spent, 'credito speso', 'crediti spesi')
    if f.refunded:
        spent += f' · {_num(f.refunded)} {pl(f.refunded, "reso", "resi")}'
    stats.append((_num(f.spent), spent))
    back = f' {_num(f.refunded)} {pl(f.refunded, "credito reso", "crediti resi")}.' if f.refunded else ''
    note = (
        f'{_num(f.undelivered)} {pl(f.undelivered, "riga non consegnata", "righe non consegnate")}: non le paghi. '
        if f.undelivered
        else ''
    ) + (f'Saldo: {_num(f.balance)} crediti.' if f.balance is not None else '')
    return layout.render(
        subject=f'La lista «{f.name}» è pronta' if f.name else 'La tua ricerca su Zenbree è pronta',
        preheader=f'{found}.{back}' if found else 'I risultati ti aspettano nella chat.',
        title='La tua lista è pronta',
        paragraphs=[f"L'agente ha finito la ricerca «{f.chat}»." if f.chat else "L'agente ha finito la ricerca."],
        stats=stats,
        note=note.strip(),
        button='Apri la lista',
        href=f.href,
        secondary=('Prepara la campagna', f.campaign_href) if f.campaign_href else None,
        reason=ASKED.format('Ricerca finita'),
        manage=True,
        locale=LOCALE,
    )


def low_credits(f: LowCredits) -> layout.Email:
    boxes = []
    if f.phones:  # a box that says «oppure 0 telefoni» reads like a bug: under one phone's price, none
        boxes.append(
            (f'Con {_num(f.credits)} crediti', f'~{_num(f.emails)} email trovate', f'oppure {_num(f.phones)} telefoni')
        )
    when = ''
    if r := f.renewal:
        when = _day(r.at)
        soon = {0: 'oggi', 1: 'domani'}.get(r.days, f'tra {r.days} giorni')
        # Not "+N": a period's allowance replaces the unused one (billing.routes), it does not add to it.
        boxes.append((f'Piano {r.plan}', f'{_num(r.credits)} crediti il {when}', soon))
    packs = ' · '.join(
        f'{_num(credits)}{" crediti" if i == 0 else ""} {euros(cents, ".", ",")}'
        for i, (credits, cents) in enumerate(f.packs)
    )
    left = pl(f.credits, 'resta 1 credito', f'restano {_num(f.credits)} crediti')
    title = f'«{f.org}»: {left}' if f.org else f'Ti {left}'
    preheader = (
        f'Bastano per circa {_num(f.emails)} email trovate.' if f.emails else 'Ricarica per non fermare le ricerche.'
    ) + (f' Il piano si rinnova il {when}.' if when else '')
    return layout.render(
        subject=title,
        preheader=preheader,
        title=title,
        paragraphs=[
            f'{f"«{f.org}» è" if f.org else "Sei"} sotto la soglia di {LOW_CREDITS} crediti. Le ricerche in corso'
            ' finiscono comunque; quelle nuove si fermano al piano se non bastano.'
        ],
        boxes=boxes,
        button='Ricarica crediti',
        href=f.href,
        secondary=(f'Passa a {f.upgrade}', f.href) if f.upgrade else None,
        fine_print=f'Ricariche: {packs}. I crediti ricaricati non scadono.' if packs else '',
        # #343: an always email, so it never claims the user turned it on.
        reason='Ricevi questa email perché riguarda i crediti della tua organizzazione. Non possiamo disattivarla.',
        manage=True,
        locale=LOCALE,
    )


def digest_subject(d: Digest) -> str:
    head = digest_head(d, APPROVALS, SUBJECT_WORDS, ' e ')
    return f'{head} · {WEEKDAYS[d.at.weekday()]} {_day(d.at)}'


def digest(d: Digest) -> layout.Email:
    """Counts only: no names, no reply text."""
    waiting = digest_lines(d, APPROVALS, DIGEST_LINES)
    credits = [
        f'Crediti disponibili: {credits}' if len(d.balances) == 1 else f'Crediti di «{org}»: {credits}'
        for org, credits in d.balances
    ]
    return layout.render(
        subject=digest_subject(d),
        preheader=', '.join(waiting),
        title=f'Ciao {d.first_name}' if d.first_name else 'Il tuo riepilogo',
        paragraphs=[*waiting, *credits],
        button='Apri Zenbree',
        href='/',
        reason=f'Ricevi questo riepilogo {"ogni lunedì" if d.weekly else "nei giorni lavorativi"}'
        ' perché hai attivato «Riepilogo via email».',
        manage=True,
        locale=LOCALE,
    )


def erasure_request(email: str) -> tuple[str, str]:
    """(subject, text) to the sender whose recipient asked, from the unsubscribe page, to be deleted."""
    return (
        'Richiesta di cancellazione dati da un destinatario',
        f'{email} ha chiesto la cancellazione dei propri dati personali (art. 17 GDPR) dalla pagina di '
        'disiscrizione delle tue campagne Zenbree.\n'
        'È già nelle tue esclusioni e non riceverà altri messaggi. Elimina i suoi dati dalle tue liste e dai '
        'tuoi sistemi e rispondigli entro un mese.\n',
    )
