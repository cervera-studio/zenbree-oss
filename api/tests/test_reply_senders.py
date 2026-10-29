"""The internal-sender boundary uses exact addresses and business domains, not shared consumer domains."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.outreach.reply_senders import same_owner


@pytest.mark.parametrize(
    ('sender', 'owner', 'internal'),
    [
        (' Owner@Gmail.COM ', 'owner@gmail.com', True),
        ('colleague@COMPANY.test', 'owner@company.test', True),
        ('lead@gmail.com', 'owner@gmail.com', False),
        ('lead@yahoo.com', 'owner@yahoo.com', False),
        ('lead@proton.me', 'owner@proton.me', False),
        ('lead@company.test.other.test', 'owner@company.test', False),
        ('lead@sub.company.test', 'owner@company.test', False),
        ('lead@client.test', '', False),
        ('', '', False),
    ],
)
def test_the_owner_and_their_business_colleagues_are_internal(sender, owner, internal):
    assert same_owner(sender, owner) is internal


LOCAL = st.text(alphabet='abcdefghijklmnopqrstuvwxyz0123456789', min_size=1, max_size=30)


@given(local=LOCAL, domain=LOCAL)
def test_normalizing_case_and_whitespace_does_not_change_the_owner_boundary(local, domain):
    owner = f'{local}@{domain}.test'
    sender = f'colleague@{domain}.test'
    assert same_owner(f' {owner.upper()} ', owner)
    assert same_owner(f' {sender.upper()} ', owner)
    assert not same_owner(f'{sender}.other.test', owner)


@given(local=LOCAL, provider=st.sampled_from(['gmail.com', 'outlook.com', 'yahoo.com', 'icloud.com', 'proton.me']))
def test_distinct_addresses_on_a_public_provider_are_not_colleagues(local, provider):
    assert not same_owner(f'{local}@{provider}', f'{local}-owner@{provider}')
