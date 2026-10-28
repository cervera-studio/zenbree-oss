"""The DSN parser (#747): Gmail's and Exchange's delivery reports, hard and soft, and every forged shape it refuses."""

import pytest

from app.outreach import dsn as reports
from app.outreach.dsn import Bounce, looks_like_dsn

ORIGINAL_RFC822 = (
    'Content-Type: message/rfc822\r\n\r\nMessage-ID: <abc@mail.example.com>\r\nSubject: Hi\r\n\r\nbody\r\n'
)
ORIGINAL_HEADERS = 'Content-Type: text/rfc822-headers\r\n\r\nMessage-ID: <abc@mail.example.com>\r\nSubject: Hi\r\n'


def status_part(*blocks: str) -> str:
    recipients = blocks or ('Final-Recipient: rfc822; Gone@Prospect.example\r\nAction: failed\r\nStatus: 5.1.1\r\n',)
    return 'Content-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; googlemail.com\r\n\r\n' + '\r\n'.join(
        recipients
    )


def dsn(
    *,
    sender: str = 'Mail Delivery Subsystem <mailer-daemon@googlemail.com>',
    headers: str = '',
    content_type: str = 'multipart/report; boundary="b1"; report-type=delivery-status',
    parts: tuple[str, ...] | None = None,
) -> bytes:
    parts = parts if parts is not None else (status_part(), ORIGINAL_RFC822)
    body = ''.join(f'--b1\r\n{p}\r\n' for p in ('Content-Type: text/plain\r\n\r\nAddress not found\r\n', *parts))
    return (
        f'From: {sender}\r\n{headers}To: seller@example.com\r\nSubject: Delivery Status Notification (Failure)\r\n'
        f'MIME-Version: 1.0\r\nContent-Type: {content_type}\r\n\r\n{body}--b1--\r\n'
    ).encode()


def read(raw: bytes, provider: str = 'google', mailbox: str = 'seller@example.com'):
    return reports.read(raw, provider=provider, mailbox=mailbox)


def block(action='failed', status='5.1.1', recipient='rfc822; gone@prospect.example') -> str:
    return f'Final-Recipient: {recipient}\r\nAction: {action}\r\nStatus: {status}\r\n'


def test_a_failed_5xx_is_a_hard_bounce_of_the_quoted_message():
    assert read(dsn()) == ([Bounce('gone@prospect.example', 'hard', '5.1.1', '<abc@mail.example.com>')], None)


def test_exchange_quotes_only_the_headers():
    assert read(dsn(parts=(status_part(), ORIGINAL_HEADERS)))[0][0].message_id == '<abc@mail.example.com>'


def test_a_delay_or_a_4xx_is_soft_and_a_delivery_is_nothing():
    assert read(dsn(parts=(status_part(block('delayed', '4.4.7')), ORIGINAL_RFC822)))[0][0].kind == 'soft'
    assert read(dsn(parts=(status_part(block('failed', '4.2.2')), ORIGINAL_RFC822)))[0][0].kind == 'soft'
    assert read(dsn(parts=(status_part(block('delivered', '2.0.0')), ORIGINAL_RFC822))) == ([], None)


def test_the_headers_tell_a_dsn_before_it_is_fetched():
    assert looks_like_dsn({'content-type': 'multipart/report; report-type=delivery-status; boundary=x'})
    assert looks_like_dsn({'from': 'Microsoft Outlook <postmaster@outlook.com>'})
    assert looks_like_dsn({'from': 'MicrosoftExchange329e@acme.test', 'subject': 'Undeliverable: Hi'})
    assert not looks_like_dsn({'from': 'someone@example.com', 'content-type': 'text/plain'})


# Each forged shape: a bounce that would refund, exclude and pause must change nothing (#747, security reviews).
FORGED = {
    # who wrote it
    'another sender': (dsn(sender='mailer-daemon@attacker.test'), 'not_from_the_provider'),
    'the provider in the display name': (
        dsn(sender='"mailer-daemon@googlemail.com" <evil@attacker.test>'),
        'not_from_the_provider',
    ),
    'two senders in one From': (
        dsn(sender='mailer-daemon@googlemail.com, evil@attacker.test'),
        'from_not_one_address',
    ),
    'two From headers': (dsn(headers='From: evil@attacker.test\r\n'), 'from_not_once'),
    # anything that came in from outside carries the receiving server's verdict, whatever it says
    'a pass verdict the sender influenced': (
        dsn(
            headers='Authentication-Results: mx.google.com; spf=pass smtp.mailfrom="dmarc=pass@x.test"; dmarc=fail\r\n'
        ),
        'received_from_outside',
    ),
    'a forged pass verdict': (
        dsn(headers='Authentication-Results: mx.google.com; dmarc=pass header.from=googlemail.com\r\n'),
        'received_from_outside',
    ),
    'an ARC seal': (
        dsn(headers='ARC-Seal: i=1; a=rsa-sha256; cv=none; d=google.com; s=arc; b=x\r\n'),
        'received_from_outside',
    ),
    # the shape
    'not a report': (
        b'From: mailer-daemon@googlemail.com\r\nContent-Type: text/plain\r\n\r\nbounce\r\n',
        'not_a_delivery_report',
    ),
    'another report type': (
        dsn(content_type='multipart/report; boundary="b1"; report-type=disposition-notification'),
        'not_a_delivery_report',
    ),
    'two report types': (
        dsn(content_type='multipart/report; boundary="b1"; report-type=x; report-type=delivery-status'),
        'not_a_delivery_report',
    ),
    'two Content-Type headers': (dsn(headers='Content-Type: text/plain\r\n'), 'content-type_not_once'),
    'two delivery-status parts': (dsn(parts=(status_part(), status_part(), ORIGINAL_RFC822)), 'parts_not_once'),
    'two originals that disagree': (
        dsn(parts=(status_part(), ORIGINAL_RFC822, ORIGINAL_HEADERS.replace('abc@', 'other@'))),
        'parts_not_once',
    ),
    'no original': (dsn(parts=(status_part(),)), 'parts_not_once'),
    'a report nested in the quoted original': (
        dsn(
            parts=(
                'Content-Type: message/rfc822\r\n\r\nMessage-ID: <abc@mail.example.com>\r\n'
                'Content-Type: multipart/report; boundary="b2"; report-type=delivery-status\r\n\r\n'
                f'--b2\r\n{status_part()}\r\n--b2--\r\n',
            )
        ),
        'parts_not_once',
    ),
    'two Message-IDs in the original': (
        dsn(parts=(status_part(), ORIGINAL_HEADERS + 'Message-ID: <other@mail.example.com>\r\n')),
        'message-id_not_once',
    ),
    'two recipients': (
        dsn(parts=(status_part(block(), block(recipient='rfc822; x@y.test')), ORIGINAL_RFC822)),
        'recipients_not_one',
    ),
    'two Final-Recipient fields': (
        dsn(parts=(status_part(block() + 'Final-Recipient: rfc822; x@y.test\r\n'), ORIGINAL_RFC822)),
        'final-recipient_not_once',
    ),
    'two Status fields': (
        dsn(parts=(status_part(block() + 'Status: 2.0.0\r\n'), ORIGINAL_RFC822)),
        'status_not_once',
    ),
    'a recipient that is not one address': (
        dsn(parts=(status_part(block(recipient='rfc822; a@b.test, c@d.test')), ORIGINAL_RFC822)),
        'bad_recipient',
    ),
    'a status that is no status': (dsn(parts=(status_part(block(status='5.1.1 x')), ORIGINAL_RFC822)), 'bad_status'),
    'an unclosed multipart': (dsn().replace(b'--b1--', b''), 'not_a_delivery_report'),
}


@pytest.mark.parametrize('forged', FORGED, ids=FORGED)
def test_a_forged_or_ambiguous_report_is_refused(forged):
    raw, reason = FORGED[forged]
    assert read(raw) == ([], reason)


def test_microsoft_reports_come_from_the_tenants_postmaster():
    def refused(sender):
        return read(dsn(sender=sender), provider='microsoft', mailbox='me@contoso.test')[1]

    assert refused('postmaster@contoso.test') is None
    assert refused('Microsoft Outlook <MicrosoftExchange329e71ec@contoso.test>') is None
    assert refused('postmaster@outlook.com') is None
    assert refused('postmaster@fabrikam.test') == 'not_from_the_provider'
    assert refused('mailer-daemon@googlemail.com') == 'not_from_the_provider'
