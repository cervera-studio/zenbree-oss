"""The two signed reply address forms: the campaign form minted today and the old owner form still parsed."""

import uuid

from app.core.reply_address import (
    ReplyKey,
    build_campaign_reply_address,
    build_reply_address,
    parse_reply_address,
)

DOMAIN, SECRET = 'in.zenbree.test', 'reply-secret'


def _id(first: str) -> str:
    return str(uuid.UUID(first + uuid.uuid4().hex[1:]))


def test_each_form_parses_back_to_what_it_names():
    campaign, owner = _id('a'), _id('b')
    new = build_campaign_reply_address(campaign, domain=DOMAIN, secret=SECRET)
    old = build_reply_address(owner, domain=DOMAIN, secret=SECRET)
    assert new is not None and old is not None
    assert parse_reply_address(f'Zenbree <{new.upper()}>', secret=SECRET) == ReplyKey(campaign, campaign=True)
    assert parse_reply_address(old, secret=SECRET) == ReplyKey(owner, campaign=False)
    assert len(new.partition('@')[0]) < 64  # RFC 5321 local-part limit


def test_an_old_address_whose_owner_id_starts_with_the_campaign_mark_stays_an_owners():
    """``c`` is a hex digit: one owner in 16 has an id that starts with it."""
    owner = _id('c')
    old = build_reply_address(owner, domain=DOMAIN, secret=SECRET)
    assert old is not None and old.startswith('reply+c')
    assert parse_reply_address(old, secret=SECRET) == ReplyKey(owner, campaign=False)


def test_a_signature_never_verifies_in_the_other_form():
    some = _id('d')
    old = build_reply_address(some, domain=DOMAIN, secret=SECRET)
    new = build_campaign_reply_address(some, domain=DOMAIN, secret=SECRET)
    assert old is not None and new is not None
    assert parse_reply_address('reply+c' + old.removeprefix('reply+'), secret=SECRET) is None
    assert parse_reply_address('reply+' + new.removeprefix('reply+c'), secret=SECRET) is None
    assert parse_reply_address(new, secret='other') is None
    assert build_campaign_reply_address(some, domain='', secret=SECRET) is None


def test_a_non_ascii_signature_is_not_ours():
    address = build_campaign_reply_address(_id('a'), domain=DOMAIN, secret=SECRET)
    assert address is not None
    local, _, domain = address.partition('@')
    forged = local.rpartition('.')[0] + '.é' + '@' + domain
    assert parse_reply_address(forged, secret=SECRET) is None
