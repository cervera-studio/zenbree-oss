"""Properties of the DSN parser (#747) and of the scope check that decides whether a mailbox reads its bounces."""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.integrations.providers import GOOGLE, MICROSOFT
from app.outreach.dsn import read
from tests.test_dsn import ORIGINAL_RFC822, block, dsn, status_part

FAST = settings(max_examples=200, derandomize=True, database=None, deadline=None)

actions = st.sampled_from(['failed', 'delayed', 'delivered', 'relayed', 'expanded'])
# RFC 3463 classes: 2 success, 4 persistent transient, 5 permanent
statuses = st.builds('{}.{}.{}'.format, st.sampled_from([2, 4, 5]), st.integers(0, 7), st.integers(0, 9))
locals_ = st.from_regex(r'[A-Za-z0-9][A-Za-z0-9._-]{0,20}', fullmatch=True)


@FAST
@given(raw=st.binary(max_size=2_000), provider=st.sampled_from(['google', 'microsoft']))
def test_any_bytes_give_a_bounce_or_a_reason_never_both_and_never_raise(raw, provider):
    bounces, refused = read(raw, provider=provider, mailbox='seller@example.com')
    assert len(bounces) <= 1 and not (bounces and refused)


@FAST
@given(action=actions, status=statuses, local=locals_)
def test_only_a_failed_5xx_is_hard_and_the_recipient_reads_back_lowercased(action, status, local):
    raw = dsn(parts=(status_part(block(action, status, f'rfc822; {local}@Prospect.example')), ORIGINAL_RFC822))
    bounces, refused = read(raw, provider='google', mailbox='seller@example.com')
    assert refused is None
    hard = action == 'failed' and status.startswith('5')
    soft = action == 'delayed' or (action == 'failed' and status.startswith('4'))
    assert [b.kind for b in bounces] == (['hard'] if hard else ['soft'] if soft else [])
    assert all(b.recipient == f'{local}@prospect.example'.lower() for b in bounces)


@FAST
@given(
    local=locals_.filter(lambda s: s.lower() != 'mailer-daemon'), domain=st.sampled_from(['googlemail.com', 'x.test'])
)
def test_a_report_from_anyone_but_the_provider_is_no_bounce(local, domain):
    bounces, refused = read(dsn(sender=f'<{local}@{domain}>'), provider='google', mailbox='seller@example.com')
    assert bounces == [] and refused


@FAST
@given(
    provider=st.sampled_from([GOOGLE, MICROSOFT]),
    prefix=st.sampled_from(['', 'https://graph.microsoft.com/', 'https://www.googleapis.com/auth/']),
    upper=st.booleans(),
    others=st.lists(st.sampled_from(['openid', 'email', 'offline_access', 'User.Read']), max_size=4),
)
def test_a_scope_is_granted_by_its_last_segment_in_any_case_and_only_by_it(provider, prefix, upper, others):
    read_scope = provider.read_scope.rsplit('/', 1)[-1]
    echoed = prefix + (read_scope.upper() if upper else read_scope)
    assert provider.grants_read([*others, echoed])
    assert not provider.grants_read(others)
    assert provider.grants_send([*others, echoed]) is False
