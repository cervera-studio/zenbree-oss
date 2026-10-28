import pytest

from app.core.locale import LOCALES
from app.notifications import push
from app.notifications import service as n

BROWSER_KINDS = [k for k, v in n.KINDS.items() if v.browser]


@pytest.mark.parametrize('locale', LOCALES)
@pytest.mark.parametrize('kind', BROWSER_KINDS)
def test_every_kind_with_a_browser_alert_has_push_text(kind, locale):
    title, body = push.text(kind, {}, locale)
    assert title.strip() and body.strip()


@pytest.mark.parametrize('locale', [locale for locale in LOCALES if locale != 'en'])
@pytest.mark.parametrize('kind', BROWSER_KINDS)
def test_each_language_has_its_own_push_text(kind, locale):
    en, own = push.text(kind, {'count': 3}, 'en'), push.text(kind, {'count': 3}, locale)
    assert en[0] != own[0]
    assert en[1] != own[1]


@pytest.mark.parametrize(
    ('locale', 'count', 'body'),
    [
        ('it', 1, '1 messaggio aspetta la tua approvazione.'),
        ('it', 4, '4 messaggi aspettano la tua approvazione.'),
        ('en', None, 'Some messages are waiting for your approval.'),
    ],
)
def test_the_approval_alert_agrees_with_the_count(locale, count, body):
    assert push.text('approval_needed', {'count': count}, locale)[1] == body


def test_the_campaign_is_named_or_left_out():
    assert push.text('reply', {'name': 'Ada', 'campaign': 'Q4'}, 'en')[1] == 'Ada replied to the campaign "Q4".'
    assert push.text('reply', {}, 'en')[1] == 'A contact replied to one of your campaigns.'
    assert (
        push.text('bounces_high', {'campaign': 'Q4'}, 'it')[1] == 'Molti indirizzi non sono validi nella campagna «Q4».'
    )
    assert push.text('bounces_high', {}, 'it')[1] == 'Molti indirizzi non sono validi in una tua campagna.'


def test_an_unverified_reply_says_so_in_the_push():
    assert (
        push.text('reply', {'name': 'Ada', 'unverified': True}, 'en')[1]
        == 'Ada replied to one of your campaigns. Sender not verified.'
    )
    assert push.text('reply', {'name': 'Ada', 'unverified': True}, 'it')[1].endswith(' Mittente non verificato.')


def test_an_unknown_kind_raises():
    with pytest.raises(ValueError, match='nope'):
        push.text('nope', {}, 'en')
