import pytest

from app.outreach.domain.campaign import MAX_STEPS, validate_steps


def step(number=1, **changes):
    return {
        'step': number,
        'channel': 'email',
        'subject': 'Hello',
        'body': 'Hello {{first_name}}',
        'send_after_days': number - 1,
        **changes,
    }


def test_valid_steps_preserve_copy_and_support_mixed_channels():
    assert validate_steps([]) == []
    raw = [
        step(body='  Keep my approved copy.  '),
        step(2, subject='', threaded=True),
        step(3, channel='linkedin_connect', subject='', body=''),
        step(4, channel='linkedin_message', subject=''),
    ]
    result = validate_steps(raw)
    assert result[0]['body'] == raw[0]['body']
    assert result[1]['threaded'] is True and result[2]['body'] == ''
    assert len(validate_steps([step(i) for i in range(1, MAX_STEPS + 1)])) == MAX_STEPS


@pytest.mark.parametrize(
    'raw',
    [
        {},
        None,
        [None],
        [step(i) for i in range(1, MAX_STEPS + 2)],
        [step(step=2)],
        [step(step=True)],
        [step(send_after_days='0')],
        [step(send_after_days=-1)],
        [step(), step(2, send_after_days=2), step(3, send_after_days=1)],
        [step(subject=' ')],
        [step(body=' ')],
        [step(body='x' * 4001)],
        [step(unknown=True)],
        [step(channel='linkedin')],
        [step(channel='linkedin_message', subject='')],
        [step(channel='linkedin_connect')],
        [step(channel='linkedin_connect', subject='', threaded=True)],
        [step(subject='', threaded=True)],
        [step(), step(2, threaded=True)],
        [step(personalised='not in body')],
        [
            step(channel='linkedin_connect', subject='', body=''),
            step(2, channel='linkedin_message', subject=''),
            step(3, channel='linkedin_message', subject='', send_after_days=1),
        ],
    ],
)
def test_invalid_steps_are_refused_without_silent_repair(raw):
    with pytest.raises(ValueError):  # noqa: PT011 - each case breaks a different rule; refusal is the contract
        validate_steps(raw)
