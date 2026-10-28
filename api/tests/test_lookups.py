"""#659: how a row's stored lookup values read, and what a reader may do with them."""

import pytest

from app.lists.lookups import deliverable, email_lookup


def test_an_email_asked_again_after_a_refund_reads_its_answer():
    # asked again by row id after a refund: the answer wins, unless it is no answer (#659 review)
    refunded = {'email': 'refunded'}
    assert email_lookup({'email': None, 'email_status': 'not_found'}, refunded).model_dump() == {'state': 'not_found'}
    assert email_lookup({'email': None, 'email_status': 'failed'}, refunded).model_dump() == {'state': 'refunded'}


@pytest.mark.parametrize(
    ('status', 'sent'),
    [('verified', True), ('catch_all', True), ('mx_ok', True), ('unknown', True), ('invalid', False)],
)
def test_a_campaign_sends_to_and_a_charge_bills_every_found_address_but_an_invalid_one(status, sent):
    assert deliverable(email_lookup({'email': 'a@acme.it', 'email_status': status}, {})) is sent
