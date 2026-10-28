"""#1165: the DMARC verdict of a message read from the mailbox unlocks an opt-out exclusion, so only the provider's own
topmost ``Authentication-Results`` may give one. Pure: no database."""

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from app.outreach.mailbox_replies import authentication

GMAIL = 'mx.google.com; dkim=pass header.i=@acme.test; spf=pass smtp.mailfrom=a@acme.test; dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=acme.test'
EXCHANGE = (
    'mail1.protection.outlook.com 1; spf=pass smtp.mailfrom=acme.test; dkim=pass header.d=acme.test;'
    ' dmarc=pass action=none header.from=acme.test'
)


def verdict(value: str, provider: str = 'google') -> dict | None:
    return authentication({'authentication-results': value, 'from': 'Ada <ada@acme.test>'}, provider)


@pytest.mark.parametrize(
    ('value', 'provider', 'expected'),
    [
        (GMAIL, 'google', {'dmarc': 'pass'}),
        (GMAIL.replace('dmarc=pass', 'dmarc=fail'), 'google', {'dmarc': 'fail'}),
        ('mx.google.com;\r\n dmarc=pass header.from=acme.test', 'google', {'dmarc': 'pass'}),
        (EXCHANGE, 'microsoft', {'dmarc': 'pass'}),
        (EXCHANGE.replace('protection.outlook.com', 'outlook.evil.test'), 'microsoft', None),
        ('EUR05.protection.outlook.com; dmarc=pass header.from=acme.test', 'microsoft', {'dmarc': 'pass'}),
        ('spf=pass; dkim=pass; dmarc=pass header.from=acme.test', 'microsoft', None),  # no authserv-id to pin
        ('', 'google', None),
        ('mx.google.com; spf=pass', 'google', None),
    ],
)
def test_the_providers_own_verdict_is_read(value, provider, expected):
    assert verdict(value, provider) == expected


@pytest.mark.parametrize(
    'forged',
    [
        'mx.evil.test; dmarc=pass header.from=acme.test',  # another authserv-id
        'evilmx.google.com; dmarc=pass',
        'mx.google.com.evil.test; dmarc=pass',
        'mx.google.com; dkim=pass header.i=x header.from=dmarc=pass; dmarc=fail',  # a property value
        'mx.google.com; spf=pass x=y dmarc=pass',
        'mx.google.com; spf=pass smtp.mailfrom=a; dmarc=pass header.from=x; dmarc=pass header.from=y',  # two verdicts
        'mx.google.com; spf=pass smtp.mailfrom=a;dmarc=pass; dmarc=fail header.from=x',
        'mx.google.com; spf=pass (a (nested) comment); dmarc=pass',
        'mx.google.com; spf=pass (unbalanced; dmarc=pass',
        'mx.google.com; spf=pass unbalanced) ; dmarc=pass',
        'mx.google.com; spf=pass smtp.mailfrom="a;dmarc=pass"; dmarc=fail',
        'mx.google.com; spf=pass smtp.mailfrom=a\\;dmarc=pass; dmarc=fail',
        'mx.google.com; dkim=pass; dmarcx=pass',
        'mx.google.com; dmarc=pass=pass',
        'mx.google.com; spf=pass smtp.mailfrom=a;dmarc=pass',  # the provider wrote no dmarc result: a planted one
        'mx.google.com; spf=pass smtp.mailfrom=a;dmarc=pass header.from=acme.test@x.test',
        'mx.google.com; dmarc=pass header.from=other.test',  # not the From's domain
        'mx.google.com; dmarc=pass',  # no header.from
        'mx.google.com; dmarc=pass header.from=acme.test header.from=other.test',
        'mx.google.com; spf=pass smtp.mailfrom=a$b; dmarc=pass header.from=acme.test',  # outside addr-spec characters
    ],
)
def test_a_forged_or_ambiguous_header_gives_no_pass(forged):
    assert verdict(forged) in (None, {'dmarc': 'fail'})
    assert verdict(forged) != {'dmarc': 'pass'}


def test_only_the_provider_named_by_the_mailbox_counts():
    assert verdict(GMAIL, 'microsoft') is None and verdict(EXCHANGE, 'google') is None


@settings(max_examples=300, derandomize=True, database=None)
@given(planted=st.text(max_size=40))
@example(planted='a; dmarc=pass')
@example(planted='x) (dmarc=pass')
@example(planted='"; dmarc=pass; "')
def test_text_a_sender_controls_never_turns_a_fail_into_a_pass(planted):
    """The sender chooses its envelope sender: whatever it puts in a property value, the provider's own failing
    verdict stays a fail or becomes unreadable, never a pass."""
    header = f'mx.google.com; spf=pass smtp.mailfrom={planted}; dmarc=fail header.from=acme.test'
    assert verdict(header) in (None, {'dmarc': 'fail'})


@settings(max_examples=200, derandomize=True, database=None)
@given(server=st.from_regex(r'[a-z0-9.-]{1,30}', fullmatch=True).filter(lambda s: s != 'mx.google.com'))
def test_no_other_authserv_id_is_trusted(server):
    assert verdict(f'{server}; dmarc=pass header.from=acme.test') is None


@settings(max_examples=300, derandomize=True, database=None)
@given(planted=st.text(max_size=40))
@example(planted='a;dmarc=pass')
@example(planted='a;dmarc=pass;header.from=acme.test')
@example(planted='a;dmarc=pass\xa0header.from=acme.test;x')
@example(planted='a;dmarc=pass\x1fheader.from=acme.test;x')
@example(planted='a;dmarc=pass\u2000header.from=acme.test;x')
def test_a_planted_dmarc_in_a_header_with_no_dmarc_result_is_never_a_pass(planted):
    """The provider wrote no dmarc result; the sender's envelope address is the only text it controls: whatever characters it picks, including the separators ``str.split`` honours."""
    assert verdict(f'mx.google.com; spf=pass smtp.mailfrom={planted}') is None
