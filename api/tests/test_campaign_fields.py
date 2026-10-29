import pytest

from app.outreach import sending
from app.outreach.fields import CampaignField, FieldsRefused, Inputs, _token, parse_fields, resolve, sanitize
from app.outreach.freeze import frozen_values
from app.outreach.step_copy import StepsRefused, checked_steps

PERSON = {'first_name': 'Anna', 'last_name': 'Rossi', 'company_name': 'Acme', 'role': 'CEO', 'email': 'a@acme.test'}


def _field(name='recentNews', key='recent_news', fallback=None, kind='column') -> CampaignField:
    return CampaignField(token=name, source={'kind': kind, 'key': key}, fallback=fallback)


def _email(subject='', body='b') -> dict:
    return {'step': 1, 'channel': 'email', 'subject': subject, 'body': body, 'send_after_days': 0}


def test_a_value_is_one_line_of_at_most_500_characters_with_no_token_or_placeholder():
    assert sanitize('  a new\n\toffice  {{firstName}} in [Milan] ') == 'a new office firstName in Milan'
    assert sanitize('x' * 499 + ' yz') == 'x' * 499
    assert len(sanitize('y' * 900)) == 500
    assert sanitize(['Rome', None, ' Milan ']) == 'Rome, Milan'
    assert sanitize(42) == '42'
    assert sanitize(None) == sanitize({'a': 1}) == ''


def test_a_required_field_with_no_value_is_missing_and_a_fallback_fills_it():
    fields = [_field(), _field('city', 'city', fallback=' your city ')]
    empty = resolve(fields, PERSON, Inputs({'recent_news': ' '}, {}))
    assert empty.missing == ['recentNews']
    assert (empty.values['recentNews'], empty.values['city']) == ('', 'your city')
    full = resolve(fields, PERSON, Inputs({'recent_news': 'IPO', 'city': 'Rome'}, {}))
    assert (full.missing, full.values['recentNews'], full.values['city']) == ([], 'IPO', 'Rome')
    assert resolve(fields, PERSON, None).missing == ['recentNews']


def test_a_signal_field_reads_the_signal_not_the_row():
    resolved = resolve(
        [_field('signalTitle', 'title', kind='signal')], PERSON, Inputs({'title': 'row'}, {'title': 'SDR'})
    )
    assert resolved.values['signalTitle'] == 'SDR'


ALIASES = {
    'firstName': 'Anna',
    'first_name': 'Anna',
    'lastName': 'Rossi',
    'last_name': 'Rossi',
    'companyName': 'Acme',
    'company_name': 'Acme',
    'company': 'Acme',
    'role': 'CEO',
    'title': 'CEO',
    'email': 'a@acme.test',
}


@pytest.mark.parametrize(('token', 'value'), ALIASES.items())
def test_each_alias_renders_as_before_for_clean_values(token, value):
    frozen = {**PERSON, 'personalization': {'values': resolve([], PERSON, None).values}}
    for values in (frozen_values(frozen), frozen_values({**PERSON, 'personalization': None})):
        assert sending.render_values(f'Hi {{{{{token}}}}}', values).value == f'Hi {value}'


def test_every_alias_is_a_contact_token():
    assert ALIASES.keys() == sending.CONTACT_TOKENS.keys()


def test_the_saved_check_counts_a_fallback_at_its_length():
    fields = [_field(fallback='f' * 200)]
    with pytest.raises(StepsRefused) as e:
        checked_steps([_email(subject='x' * 101 + '{{recentNews}}')], fields=fields)
    assert (e.value.code, e.value.step) == ('CAMPAIGN.COPY_TOO_LONG', 1)
    assert checked_steps([_email(subject='x' * 100 + '{{recentNews}}')], fields=fields)
    note = {'step': 1, 'channel': 'linkedin_connect', 'body': 'x' * 280 + ' {{recentNews}}', 'send_after_days': 0}
    with pytest.raises(StepsRefused) as e:
        checked_steps([note], agent_copy=True, fields=fields)
    assert (e.value.code, e.value.step) == ('CAMPAIGN.COPY_TOO_LONG', 1)
    assert checked_steps([note], agent_copy=True, fields=[_field(fallback='f' * 5)])


def test_the_saved_check_counts_a_required_field_at_its_token_length():
    assert checked_steps([_email(subject='x' * 286 + '{{recentNews}}')], fields=[_field()])
    with pytest.raises(StepsRefused):
        checked_steps([_email(subject='x' * 287 + '{{recentNews}}')], fields=[_field()])


def test_agent_copy_takes_the_campaigns_field_tokens_and_no_other():
    assert checked_steps([_email('Re {{recentNews}}', 'Hi {{firstName}}')], agent_copy=True, fields=[_field()])
    with pytest.raises(StepsRefused) as e:
        checked_steps([_email('Re {{recentNews}}')], agent_copy=True)
    assert 'unknown placeholder {{recentNews}}' in e.value.message


@pytest.mark.parametrize(
    ('raw', 'reason'),
    [
        ([{'token': 'firstName', 'source': {'kind': 'column', 'key': 'nickname'}}], 'contact token'),
        ([{'token': 'x', 'source': {'kind': 'column', 'key': 'city'}}], 'pattern'),
        ([{'token': 'jobTitle', 'source': {'kind': 'signal', 'key': 'salary'}}], 'signal field'),
        ([{'token': 'vendor', 'source': {'kind': 'column', 'key': 'apollo_id'}}], 'not one a user sees'),
        ([{'token': 'city', 'source': {'kind': 'column', 'key': 'city'}, 'fallback': '  '}], 'at least 1'),
        ([{'token': 'city', 'source': {'kind': 'column', 'key': 'city'}}] * 2, 'two fields'),
    ],
)
def test_a_bad_field_is_refused_with_its_reason(raw, reason):
    with pytest.raises(FieldsRefused) as e:
        parse_fields(raw)
    assert reason in str(e.value)


@pytest.mark.parametrize(
    ('key', 'token'),
    [
        ('recent_news', 'recentNews'),
        ('città', 'citt'),
        ('2024 revenue', 'field2024Revenue'),
        ('é', 'field'),
        ('a', 'a2'),
        ('k' * 80, 'k' * 36),
    ],
)
def test_a_catalog_token_passes_the_field_pattern_for_any_column_key(key, token):
    assert _token(key, set()) == token
    assert _field(name=token)


def test_a_catalog_token_never_repeats_or_takes_a_contact_token():
    taken: set[str] = set()
    assert [_token(k, taken) for k in ('recent news', 'recent_news', 'company')] == [
        'recentNews',
        'recentNews2',
        'company2',
    ]
