"""The service emails in French."""

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
)

LOCALE: Locale = 'fr'
NBSP = ' '  # before « : », « ? » and inside « guillemets », as French typography asks
ASKED = f'Vous recevez cet e-mail parce que vous avez activé «{NBSP}{{}}{NBSP}» dans vos notifications.'
LABELS = {'interested': 'intéressé', 'meeting_request': 'demande de rendez-vous'}
MONTHS = ('janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.')
WEEKDAYS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
APPROVALS: Words = ('message à approuver', 'messages à approuver')
DIGEST_LINES: dict[str, Words] = {
    'reply_urgent': ('réponse intéressée ou demande de rendez-vous', 'réponses intéressées ou demandes de rendez-vous'),
    'reply': ('autre réponse à vos campagnes', 'autres réponses à vos campagnes'),
    'linkedin_accepted': ('invitation LinkedIn acceptée', 'invitations LinkedIn acceptées'),
    'search_done': ('recherche terminée', 'recherches terminées'),
    'search_failed': ('recherche arrêtée', 'recherches arrêtées'),
}
SUBJECT_WORDS: dict[str, Words] = {
    'reply': ('réponse', 'réponses'),
    'linkedin_accepted': ('invitation acceptée', 'invitations acceptées'),
    'search_done': DIGEST_LINES['search_done'],
    'search_failed': DIGEST_LINES['search_failed'],
}


def _pl(n: int, one: str, many: str) -> str:
    # French counts 0 and 1 in the singular: « 0 crédit dépensé ».
    return one if n in (0, 1) else many


def _num(n: int) -> str:
    return grouped(n, ' ')


def _day(at: datetime) -> str:
    return f'{"1er" if at.day == 1 else at.day} {MONTHS[at.month - 1]}'


def _quoted(text: str) -> str:
    return f'«{NBSP}{text}{NBSP}»'


def reply_urgent(href: str, data: dict) -> layout.Email:
    who = contact(data, 'Un contact')
    label = LABELS.get(data.get('label', ''), data.get('label', ''))
    campaign = one_line(data.get('campaign'))
    where = f'à la campagne {_quoted(campaign)}' if campaign else 'à votre campagne'
    return layout.render(
        subject='Nouvelle réponse intéressée sur Zenbree',
        preheader=f'Étiquette{NBSP}: {label}. La réponse est dans Zenbree.',
        title='Vous avez une réponse intéressée',
        paragraphs=[
            f'{who} a répondu {where} par {data.get("channel") or "e-mail"}. Étiquette{NBSP}: {label}.',
            'Le texte de la réponse reste dans Zenbree.',
        ],
        button='Ouvrir et répondre',
        href=href,
        secondary=('Voir le brouillon', f'{href}#reply'),
        reason=ASKED.format('Réponse intéressée ou demande de rendez-vous'),
        manage=True,
        locale=LOCALE,
    )


def search_failed(href: str, data: dict) -> layout.Email:
    chat = one_line(data.get('chat'))
    done, units = data.get('done'), data.get('units')
    where = f'La recherche {_quoted(chat)}' if chat else 'Votre recherche'
    progress = f' après {done} sur {units}' if units else ''
    return layout.render(
        subject='Recherche arrêtée sur Zenbree',
        preheader='Un fournisseur ne répond pas. Vous ne payez pas ce qui manque.',
        title='Votre recherche s’est arrêtée',
        paragraphs=[
            f'{where} s’est arrêtée{progress}{NBSP}: un fournisseur ne répond pas.',
            'Ce qui manque ne vous a pas été facturé. Réessayez depuis la conversation quand vous voulez.',
        ],
        button='Ouvrir la conversation',
        href=href,
        reason=ASKED.format('Recherche arrêtée ou choix à faire'),
        manage=True,
        locale=LOCALE,
    )


def bounces_high(href: str, data: dict) -> layout.Email:
    campaign = one_line(data.get('campaign'))
    invalid, attempted = data.get('invalid'), data.get('attempted')
    what = f'La campagne {_quoted(campaign)}' if campaign else 'L’une de vos campagnes'
    return layout.render(
        subject=f'Trop de rebonds dans la campagne {_quoted(campaign)}'
        if campaign
        else 'Trop de rebonds dans une campagne',
        preheader='De nombreuses adresses ne sont pas valides. Vérifiez la liste avant de continuer.',
        title='Trop d’adresses non valides',
        paragraphs=[
            f'{what} a eu {invalid} {_pl(invalid or 0, "e-mail rejeté", "e-mails rejetés")} sur {attempted} '
            f'{_pl(attempted or 0, "envoyé", "envoyés")} : nous l’avons mise en pause.'
            if data.get('paused')
            else f'{what} compte {invalid} {_pl(invalid or 0, "adresse non valide", "adresses non valides")} sur '
            f'{attempted} {_pl(attempted or 0, "personne", "personnes")} à contacter.',
            'Un taux de rebond élevé nuit à la réputation de vos boîtes mail. '
            'Vérifiez les adresses de la liste avant de continuer.',
        ],
        button='Ouvrir la campagne',
        href=href,
        reason='Vous recevez cet e-mail parce qu’il concerne la réputation de vos boîtes mail. '
        'Il ne peut pas être désactivé.',
        manage=True,
        locale=LOCALE,
    )


def mailbox_error(href: str, data: dict) -> layout.Email:
    mailbox = one_line(data.get('mailbox'))
    linkedin = data.get('provider') == 'unipile'
    account = _quoted(mailbox) if mailbox else 'Votre compte LinkedIn' if linkedin else 'L’une de vos boîtes mail'
    return layout.render(
        subject='Compte LinkedIn déconnecté sur Zenbree' if linkedin else 'Boîte mail déconnectée sur Zenbree',
        preheader='Les campagnes LinkedIn n’envoient rien tant que vous ne l’avez pas reconnecté.'
        if linkedin
        else 'Les campagnes qui l’utilisent n’envoient rien tant que vous ne l’avez pas reconnectée.',
        title='Un compte LinkedIn n’est plus connecté' if linkedin else 'Une boîte mail n’est plus connectée',
        paragraphs=[
            f'{account} ne répond plus à Zenbree.',
            'Tant que vous ne l’avez pas reconnecté, les campagnes LinkedIn n’envoient ni invitations ni messages.'
            if linkedin
            else 'Tant que vous ne l’avez pas reconnectée, les campagnes qui l’utilisent n’envoient rien depuis '
            'cette boîte mail.',
        ],
        button='Reconnecter le compte' if linkedin else 'Reconnecter la boîte mail',
        href=href,
        reason='Vous recevez cet e-mail parce qu’il concerne vos envois. Il ne peut pas être désactivé.',
        manage=True,
        locale=LOCALE,
    )


def payment_failed(href: str, data: dict) -> layout.Email:
    # #323: an admin of several organizations must know which one.
    org = one_line(data.get('organization'))
    return layout.render(
        subject='La recharge automatique a échoué',
        preheader='La recharge automatique a été désactivée.',
        title='La recharge automatique a échoué',
        paragraphs=[
            f'La recharge automatique de crédits{f" de {_quoted(org)}" if org else ""} a échoué et a été désactivée.',
            'Vérifiez votre moyen de paiement et réactivez-la.',
        ],
        button='Vérifier le paiement',
        href=href,
        reason='Vous recevez cet e-mail parce que la recharge automatique est activée sur votre compte. '
        f'Il ne peut pas être désactivé{NBSP}: il concerne les paiements.',
        locale=LOCALE,
    )


def search_done(f: SearchDone) -> layout.Email:
    stats = [(_num(f.people), _pl(f.people, 'personne', 'personnes'))] * bool(f.people)
    stats += [(_num(f.companies), _pl(f.companies, 'entreprise', 'entreprises'))] * bool(f.companies)
    found = ', '.join(f'{n} {label}' for n, label in stats)
    spent = _pl(f.spent, 'crédit dépensé', 'crédits dépensés')
    if f.refunded:
        spent += f' · {_num(f.refunded)} {_pl(f.refunded, "restitué", "restitués")}'
    stats.append((_num(f.spent), spent))
    back = f' {_num(f.refunded)} {_pl(f.refunded, "crédit restitué", "crédits restitués")}.' if f.refunded else ''
    note = (
        f'{_num(f.undelivered)} {_pl(f.undelivered, "ligne non livrée", "lignes non livrées")}{NBSP}: '
        'vous ne les payez pas. '
        if f.undelivered
        else ''
    ) + (f'Solde{NBSP}: {_num(f.balance)} {_pl(f.balance, "crédit", "crédits")}.' if f.balance is not None else '')
    return layout.render(
        subject=f'Votre liste {_quoted(f.name)} est prête' if f.name else 'Votre recherche Zenbree est prête',
        preheader=f'{found}.{back}' if found else 'Vos résultats vous attendent dans la conversation.',
        title='Votre liste est prête',
        paragraphs=[
            f'L’agent a terminé la recherche {_quoted(f.chat)}.' if f.chat else 'L’agent a terminé la recherche.'
        ],
        stats=stats,
        note=note.strip(),
        button='Ouvrir la liste',
        href=f.href,
        secondary=('Préparer la campagne', f.campaign_href) if f.campaign_href else None,
        reason=ASKED.format('Recherche terminée'),
        manage=True,
        locale=LOCALE,
    )


def low_credits(f: LowCredits) -> layout.Email:
    boxes = []
    if f.phones:  # a box that says « ou 0 téléphone » reads like a bug: under one phone's price, none
        boxes.append(
            (
                f'Avec {_num(f.credits)} crédits',
                f'~{_num(f.emails)} e-mails trouvés',
                f'ou {_num(f.phones)} {_pl(f.phones, "téléphone", "téléphones")}',
            )
        )
    when = ''
    if r := f.renewal:
        when = _day(r.at)
        soon = {0: 'aujourd’hui', 1: 'demain'}.get(r.days, f'dans {r.days} jours')
        # Not "+N": a period's allowance replaces the unused one (billing.routes), it does not add to it.
        boxes.append((f'Forfait {r.plan}', f'{_num(r.credits)} crédits le {when}', soon))
    packs = ' · '.join(
        f'{_num(credits)}{" crédits" if i == 0 else ""} {euros(cents, " ", ",", symbol_after=True)}'
        for i, (credits, cents) in enumerate(f.packs)
    )
    left = f'{_num(f.credits)} {_pl(f.credits, "crédit restant", "crédits restants")}'
    title = (
        f'{_quoted(f.org)}{NBSP}: {left}'
        if f.org
        else f'Il vous reste {_num(f.credits)} {_pl(f.credits, "crédit", "crédits")}'
    )
    preheader = (
        f'De quoi trouver environ {_num(f.emails)} e-mails.' if f.emails else 'Rechargez pour continuer vos recherches.'
    ) + (f' Votre forfait se renouvelle le {when}.' if when else '')
    who = f'{_quoted(f.org)} est' if f.org else 'Vous êtes'
    return layout.render(
        subject=title,
        preheader=preheader,
        title=title,
        paragraphs=[
            f'{who} sous le seuil de {LOW_CREDITS} crédits. Les recherches déjà en cours se terminent quand même'
            f'{NBSP}; les nouvelles s’arrêtent au plan si les crédits ne suffisent pas.'
        ],
        boxes=boxes,
        button='Recharger des crédits',
        href=f.href,
        secondary=(f'Passer à {f.upgrade}', f.href) if f.upgrade else None,
        fine_print=f'Recharges{NBSP}: {packs}. Les crédits rechargés n’expirent jamais.' if packs else '',
        # #343: an always email, so it never claims the user turned it on.
        reason='Vous recevez cet e-mail parce qu’il concerne les crédits de votre organisation. '
        'Il ne peut pas être désactivé.',
        manage=True,
        locale=LOCALE,
    )


def digest_subject(d: Digest) -> str:
    head = digest_head(d, APPROVALS, SUBJECT_WORDS, ' et ')
    return f'{head} · {WEEKDAYS[d.at.weekday()]} {_day(d.at)}'


def digest(d: Digest) -> layout.Email:
    """Counts only: no names, no reply text."""
    waiting = digest_lines(d, APPROVALS, DIGEST_LINES)
    credits = [
        f'Crédits disponibles{NBSP}: {credits}'
        if len(d.balances) == 1
        else f'Crédits de {_quoted(org)}{NBSP}: {credits}'
        for org, credits in d.balances
    ]
    return layout.render(
        subject=digest_subject(d),
        preheader=', '.join(waiting),
        title=f'Bonjour {d.first_name}' if d.first_name else 'Votre récapitulatif',
        paragraphs=[*waiting, *credits],
        button='Ouvrir Zenbree',
        href='/',
        reason=f'Vous recevez ce récapitulatif {"chaque lundi" if d.weekly else "les jours ouvrés"}'
        f' parce que vous avez activé {_quoted("Récapitulatif par e-mail")}.',
        manage=True,
        locale=LOCALE,
    )


def erasure_request(email: str) -> tuple[str, str]:
    """(subject, text) to the sender whose recipient asked, from the unsubscribe page, to be deleted."""
    return (
        'Un destinataire a demandé la suppression de ses données',
        f'{email} a demandé la suppression de ses données personnelles (art. 17 du RGPD) depuis la page de '
        'désinscription de vos campagnes Zenbree.\n'
        'Cette personne figure déjà dans vos exclusions et ne recevra plus de messages. Supprimez ses données de '
        'vos listes et de vos systèmes, et répondez-lui dans un délai d’un mois.\n',
    )
