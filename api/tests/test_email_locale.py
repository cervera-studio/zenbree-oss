from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.locale import LOCALES
from app.notifications import emails
from app.notifications import service as n

ITALIAN_MARKS = '«»àèéìòù’'
_CASES = [
    ('reply_urgent', {'name': 'Anna', 'label': 'meeting_request', 'campaign': 'SDR', 'channel': 'email'}),
    ('search_failed', {'chat': 'CEO in Milan', 'done': 2, 'units': 5}),
    ('bounces_high', {'campaign': 'SDR', 'invalid': 3, 'attempted': 40}),
    ('bounces_high', {'campaign': 'SDR', 'invalid': 3, 'attempted': 40, 'paused': True}),
    ('mailbox_error', {'mailbox': 'me@acme.test', 'provider': 'google'}),
    ('mailbox_error', {'mailbox': 'Anna Rossi', 'provider': 'unipile'}),
    ('payment_failed', {'organization': 'Acme'}),
]


def test_a_kind_renders_in_the_language_it_is_given():
    for kind, data in _CASES:
        italian, english = n._render(kind, '/x', data, 'it'), n._render(kind, '/x', data, 'en')
        assert '<html lang="it">' in italian.html and '<html lang="en">' in english.html
        assert english.subject != italian.subject and english.text != italian.text
        assert not any(c in english.subject + english.text for c in ITALIAN_MARKS)
        assert 'Gestisci' not in english.html and 'Zenbree' in english.html
        assert ('Manage notifications' in english.text) == (kind != 'payment_failed')


def test_english_urgent_reply_names_who_and_the_label_and_keeps_the_draft_button():
    mail = n._render(
        'reply_urgent',
        '/inbox?m=1',
        {'name': 'Anna', 'label': 'meeting_request', 'channel': 'linkedin', 'campaign': 'SDR'},
        'en',
    )
    assert mail.subject == 'New interested reply on Zenbree' and 'Anna' not in mail.subject
    assert 'Anna replied to the campaign "SDR" via linkedin. Label: meeting request.' in mail.text
    assert 'Open and reply' in mail.text and f'See the draft: {settings.web_origin}/inbox?m=1#reply' in mail.text
    assert 'You get this email because you turned on "Interested reply or meeting request"' in mail.text


def _digest(locale: n.Locale) -> n.Digest:
    return n.Digest(
        'a@x.test',
        'Anna Rossi',
        'weekly',
        {'reply': 1, 'reply_urgent': 1, 'search_done': 2},
        12,
        [('Acme', 55)],
        datetime(2026, 10, 7, 9, tzinfo=UTC),
        locale,
    )


@pytest.mark.parametrize('locale', LOCALES)
def test_every_email_renders_in_every_language(locale):
    for kind, data in _CASES:
        mail, english = n._render(kind, '/x', data, locale), n._render(kind, '/x', data, 'en')
        assert f'<html lang="{locale}">' in mail.html and mail.subject and mail.text
        if locale != 'en':
            assert mail.subject != english.subject and mail.text != english.text, kind


@pytest.mark.parametrize('locale', LOCALES)
def test_the_digest_and_the_erasure_request_exist_in_every_language(locale):
    digest = emails.digest(_digest(locale))
    assert f'<html lang="{locale}">' in digest.html and ' · ' in digest.subject
    subject, text = emails.erasure_request('anna@x.test', locale)
    assert subject and 'anna@x.test' in text
    if locale != 'en':
        assert digest.text != emails.digest(_digest('en')).text
        assert subject != emails.erasure_request('anna@x.test', 'en')[0]


def test_spanish_and_french_write_numbers_and_dates_their_way():
    data = {'list': 'CEO', 'people': 1200, 'spent': 2400, 'balance': 13000}
    assert '1.200 personas' in n._render('search_done', '/', data, 'es').text
    assert '1\u202f200 personnes' in n._render('search_done', '/', data, 'fr').text
    assert emails.digest_subject(_digest('fr')).endswith('mercredi 7 oct.')
    assert emails.digest_subject(_digest('es')).endswith('miércoles 7 oct')


def test_spanish_and_french_counts_agree_with_their_noun():
    """French counts 0 and 1 in the singular; Spanish only 1."""
    fr = n._render('search_done', '/', {'list': 'L', 'people': 2, 'spent': 0, 'balance': 1}, 'fr').text
    assert '0 crédit dépensé' in fr and 'Solde\u00a0: 1 crédit.' in fr
    assert 'Il vous reste 0 crédit' in n._render('low_credits', '/', {'credits': 0}, 'fr').text
    es = n._render('search_done', '/', {'list': 'L', 'people': 2, 'spent': 0, 'balance': 1}, 'es').text
    assert '0 créditos gastados' in es and 'Saldo: 1 crédito.' in es
    one_bad = {'campaign': 'SDR', 'invalid': 1, 'attempted': 1}
    assert '1 adresse non valide sur 1 personne' in n._render('bounces_high', '/', one_bad, 'fr').text
    assert '1 dirección no válida de 1 persona' in n._render('bounces_high', '/', one_bad, 'es').text


def test_a_pause_for_bounces_says_it_paused_the_campaign_in_every_language():
    """#747: the bounces read from the mailbox count sent emails, and the campaign is paused."""
    paused = {'campaign': 'SDR', 'invalid': 2, 'attempted': 30, 'paused': True}
    words = {'en': 'paused', 'it': 'pausa', 'es': 'pausado', 'fr': 'pause'}
    for locale in LOCALES:
        text = n._render('bounces_high', '/', paused, locale).text
        assert words[locale] in text and '2' in text and '30' in text
