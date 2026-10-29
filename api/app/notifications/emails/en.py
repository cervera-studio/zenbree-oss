"""The service emails in English, the base language: a new language starts as a copy of this module."""

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

LOCALE: Locale = 'en'
ASKED = 'You get this email because you turned on "{}" in your notifications.'
LABELS = {'interested': 'interested', 'meeting_request': 'meeting request'}
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
APPROVALS: Words = ('message to approve', 'messages to approve')
DIGEST_LINES: dict[str, Words] = {
    'reply_urgent': ('interested reply or meeting request', 'interested replies or meeting requests'),
    'reply': ('other reply to your campaigns', 'other replies to your campaigns'),
    'linkedin_accepted': ('LinkedIn invitation accepted', 'LinkedIn invitations accepted'),
    'search_done': ('search finished', 'searches finished'),
    'search_failed': ('search stopped', 'searches stopped'),
}
SUBJECT_WORDS: dict[str, Words] = {
    'reply': ('reply', 'replies'),
    'linkedin_accepted': ('invitation accepted', 'invitations accepted'),
    'search_done': DIGEST_LINES['search_done'],
    'search_failed': DIGEST_LINES['search_failed'],
}


def _num(n: int) -> str:
    return grouped(n, ',')


def _day(at: datetime) -> str:
    return f'{MONTHS[at.month - 1]} {at.day}'


def reply_urgent(href: str, data: dict) -> layout.Email:
    who = contact(data, 'A contact')
    label = LABELS.get(data.get('label', ''), data.get('label', ''))
    campaign = one_line(data.get('campaign'))
    where = f'to the campaign "{campaign}"' if campaign else 'to your campaign'
    return layout.render(
        subject='New interested reply on Zenbree',
        preheader=f'Label: {label}. The reply is in Zenbree.',
        title='You have an interested reply',
        paragraphs=[
            f'{who} replied {where} via {data.get("channel") or "email"}. Label: {label}.',
            'The text of the reply stays in Zenbree.',
        ],
        button='Open and reply',
        href=href,
        secondary=('See the draft', f'{href}#reply'),
        reason=ASKED.format('Interested reply or meeting request'),
        manage=True,
        locale=LOCALE,
    )


def search_failed(href: str, data: dict) -> layout.Email:
    chat = one_line(data.get('chat'))
    done, units = data.get('done'), data.get('units')
    where = f'The search "{chat}"' if chat else 'Your search'
    return layout.render(
        subject='Search stopped on Zenbree',
        preheader='A provider is not responding. You do not pay for what is missing.',
        title='Your search stopped',
        paragraphs=[
            f'{where} stopped{f" after {done} of {units}" if units else ""}: a provider is not responding.',
            'You were not charged for what is missing. Try again from the chat when you like.',
        ],
        button='Open the chat',
        href=href,
        reason=ASKED.format('Search stopped or your choice needed'),
        manage=True,
        locale=LOCALE,
    )


def bounces_high(href: str, data: dict) -> layout.Email:
    campaign = one_line(data.get('campaign'))
    invalid, attempted = data.get('invalid'), data.get('attempted')
    return layout.render(
        subject=f'Too many bounces in the campaign "{campaign}"' if campaign else 'Too many bounces in a campaign',
        preheader='Many addresses are not valid. Check the list before you continue.',
        title='Too many invalid addresses',
        paragraphs=[
            # #747: a pause for bounces read from the mailbox counts sent emails, not people to write to
            f'{f'The campaign "{campaign}"' if campaign else "One of your campaigns"} had {invalid} of {attempted}'
            ' sent emails bounce, so we paused it.'
            if data.get('paused')
            else f'{f'The campaign "{campaign}"' if campaign else "One of your campaigns"} has {invalid} invalid'
            f' addresses out of {attempted} people to write to.',
            'A high bounce rate hurts the reputation of your mailboxes. '
            'Check the addresses in the list before you continue.',
        ],
        button='Open the campaign',
        href=href,
        reason='You get this email because it is about the reputation of your mailboxes. It cannot be turned off.',
        manage=True,
        locale=LOCALE,
    )


def mailbox_error(href: str, data: dict) -> layout.Email:
    mailbox = one_line(data.get('mailbox'))
    linkedin = data.get('provider') == 'unipile'
    account = f'"{mailbox}"' if mailbox else 'Your LinkedIn account' if linkedin else 'One of your mailboxes'
    return layout.render(
        subject='LinkedIn account disconnected on Zenbree' if linkedin else 'Mailbox disconnected on Zenbree',
        preheader='LinkedIn campaigns do not send until you reconnect it.'
        if linkedin
        else 'Campaigns that use it do not send until you reconnect it.',
        title='A LinkedIn account is no longer connected' if linkedin else 'A mailbox is no longer connected',
        paragraphs=[
            f'{account} no longer responds to Zenbree.',
            'Until you reconnect it, LinkedIn campaigns send no invitations or messages.'
            if linkedin
            else 'Until you reconnect it, campaigns that use it send nothing from this mailbox.',
        ],
        button='Reconnect the account' if linkedin else 'Reconnect the mailbox',
        href=href,
        reason='You get this email because it is about your sending. It cannot be turned off.',
        manage=True,
        locale=LOCALE,
    )


def payment_failed(href: str, data: dict) -> layout.Email:
    # #323: an admin of several organizations must know which one.
    org = one_line(data.get('organization'))
    return layout.render(
        subject='Automatic top-up failed',
        preheader='Automatic top-up has been turned off.',
        title='Automatic top-up failed',
        paragraphs=[
            f'The automatic credit top-up{f' for "{org}"' if org else ""} failed and was turned off.',
            'Check your payment method and turn it back on.',
        ],
        button='Check the payment',
        href=href,
        reason='You get this email because automatic top-up is on for your account. '
        'It cannot be turned off: it is about payments.',
        locale=LOCALE,
    )


def search_done(f: SearchDone) -> layout.Email:
    stats = [(_num(f.people), pl(f.people, 'person', 'people'))] * bool(f.people)
    stats += [(_num(f.companies), pl(f.companies, 'company', 'companies'))] * bool(f.companies)
    found = ', '.join(f'{n} {label}' for n, label in stats)
    spent = pl(f.spent, 'credit spent', 'credits spent')
    if f.refunded:
        spent += f' · {_num(f.refunded)} refunded'
    stats.append((_num(f.spent), spent))
    back = f' {_num(f.refunded)} {pl(f.refunded, "credit", "credits")} refunded.' if f.refunded else ''
    note = (
        f"{_num(f.undelivered)} {pl(f.undelivered, 'row', 'rows')} not delivered: you don't pay for them. "
        if f.undelivered
        else ''
    ) + (f'Balance: {_num(f.balance)} credits.' if f.balance is not None else '')
    return layout.render(
        subject=f'Your list "{f.name}" is ready' if f.name else 'Your Zenbree search is ready',
        preheader=f'{found}.{back}' if found else 'Your results are waiting in the chat.',
        title='Your list is ready',
        paragraphs=[f'The agent finished the search "{f.chat}".' if f.chat else 'The agent finished the search.'],
        stats=stats,
        note=note.strip(),
        button='Open the list',
        href=f.href,
        secondary=('Prepare the campaign', f.campaign_href) if f.campaign_href else None,
        reason=ASKED.format('Search finished'),
        manage=True,
        locale=LOCALE,
    )


def low_credits(f: LowCredits) -> layout.Email:
    boxes = []
    if f.phones:  # a box that says "or 0 phone numbers" reads like a bug: under one phone's price, none
        boxes.append(
            (
                f'With {_num(f.credits)} credits',
                f'~{_num(f.emails)} emails found',
                f'or {_num(f.phones)} phone numbers',
            )
        )
    when = ''
    if r := f.renewal:
        when = _day(r.at)
        soon = {0: 'today', 1: 'tomorrow'}.get(r.days, f'in {r.days} days')
        # Not "+N": a period's allowance replaces the unused one (billing.routes), it does not add to it.
        boxes.append((f'{r.plan} plan', f'{_num(r.credits)} credits on {when}', soon))
    packs = ' · '.join(
        f'{_num(credits)}{" credits" if i == 0 else ""} {euros(cents, ",", ".")}'
        for i, (credits, cents) in enumerate(f.packs)
    )
    left = f'{_num(f.credits)} {pl(f.credits, "credit", "credits")} left'
    title = f'"{f.org}": {left}' if f.org else f'You have {left}'
    preheader = (f'Enough for about {_num(f.emails)} found emails.' if f.emails else 'Top up to keep searching.') + (
        f' Your plan renews on {when}.' if when else ''
    )
    who = f'"{f.org}" is' if f.org else 'You are'
    return layout.render(
        subject=title,
        preheader=preheader,
        title=title,
        paragraphs=[
            f'{who} below the {LOW_CREDITS} credit threshold. Searches'
            ' already running still finish; new ones stop at the plan if the credits are not enough.'
        ],
        boxes=boxes,
        button='Top up credits',
        href=f.href,
        secondary=(f'Upgrade to {f.upgrade}', f.href) if f.upgrade else None,
        fine_print=f'Top-ups: {packs}. Topped-up credits never expire.' if packs else '',
        # #343: an always email, so it never claims the user turned it on.
        reason="You get this email because it is about your organization's credits. It cannot be turned off.",
        manage=True,
        locale=LOCALE,
    )


def digest_subject(d: Digest) -> str:
    head = digest_head(d, APPROVALS, SUBJECT_WORDS, ' and ')
    return f'{head} · {WEEKDAYS[d.at.weekday()]} {_day(d.at)}'


def digest(d: Digest) -> layout.Email:
    """Counts only: no names, no reply text."""
    waiting = digest_lines(d, APPROVALS, DIGEST_LINES)
    credits = [
        f'Credits available: {credits}' if len(d.balances) == 1 else f'Credits for "{org}": {credits}'
        for org, credits in d.balances
    ]
    return layout.render(
        subject=digest_subject(d),
        preheader=', '.join(waiting),
        title=f'Hi {d.first_name}' if d.first_name else 'Your digest',
        paragraphs=[*waiting, *credits],
        button='Open Zenbree',
        href='/',
        reason=f'You get this digest {"every Monday" if d.weekly else "on working days"} because you turned on'
        ' "Email digest".',
        manage=True,
        locale=LOCALE,
    )


def erasure_request(email: str) -> tuple[str, str]:
    """(subject, text) to the sender whose recipient asked, from the unsubscribe page, to be deleted."""
    return (
        'A recipient asked to delete their data',
        f'{email} asked to delete their personal data (GDPR art. 17) from the unsubscribe page of your '
        'Zenbree campaigns.\n'
        'They are already in your exclusions and get no more messages. Delete their data from your lists '
        'and your systems, and reply to them within one month.\n',
    )
