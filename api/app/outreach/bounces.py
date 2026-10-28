"""What a bounce read from the sending mailbox does (#747, owner decision 2026-10-15).

A soft bounce is recorded on its send and nothing else. A hard bounce also stops the person's sequence, excludes the
address in the campaign's client (``origin = 'bounced'``), gives the email credit back (``app.billing.bounce_refund``)
when MillionVerifier, asked again at the bounce, calls the address invalid (owner decision on #919), and pauses the campaign once more than ``BOUNCE_RATE_PERCENT`` of at least
``BOUNCE_MIN_ATTEMPTS`` sent emails bounced hard, telling its owner with the urgent ``bounces_high`` notification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from app.billing import balance, bounce_refund
from app.core.db import get_conn
from app.integrations import webhooks
from app.notifications import service as notifications
from app.notifications.service import BOUNCE_MIN_ATTEMPTS, BOUNCE_RATE_PERCENT
from app.outreach import coordination
from app.outreach.coordination import Person, Signal, SignalKind
from app.outreach.dsn import Bounce
from app.outreach.reply_targets import TARGET_COLUMNS

logger = structlog.get_logger()

# The send a bounce answers: the Message-ID the provider gave it or the one we wrote, from this mailbox, to this
# recipient, not already bounced hard (a soft bounce never overwrites a hard one).
_SEND = """SELECT id FROM outreach_campaign_sends WHERE sender_id = %(mailbox)s AND channel = 'email'
    AND status = 'sent' AND lower(btrim(recipient)) = %(to)s AND %(mid)s IN (message_id, provider_message_id)
    AND %(mid)s <> '' AND (bounce_kind IS NULL OR (bounce_kind = 'soft' AND %(kind)s = 'hard'))
    ORDER BY delivered_at DESC LIMIT 1"""

_MARK = f"""UPDATE outreach_campaign_sends s SET bounced_at = now(), bounce_kind = %(kind)s, bounce_status = %(status)s
FROM outreach_campaigns c
WHERE s.id = ({_SEND}) AND c.id = s.campaign_id
RETURNING s.id::text, s.campaign_id::text, s.target_id, s.recipient, s.delivered_at, c.project_id::text,
          c.list_id::text, c.organization_id::text, c.owner_id::text"""

_PAUSE = """UPDATE outreach_campaigns c SET status = 'paused'
FROM (SELECT count(*) AS sent, count(*) FILTER (WHERE bounce_kind = 'hard') AS hard FROM outreach_campaign_sends
      WHERE campaign_id = %(id)s AND channel = 'email' AND status = 'sent') x
WHERE c.id = %(id)s AND c.status = 'active' AND x.sent >= %(min)s AND x.hard * 100 > x.sent * %(rate)s
RETURNING c.name, c.owner_id::text, x.hard, x.sent"""


def _pause_above_threshold(conn, send: dict) -> notifications.Notified | None:
    c = conn.execute(
        _PAUSE, {'id': send['campaign_id'], 'min': BOUNCE_MIN_ATTEMPTS, 'rate': BOUNCE_RATE_PERCENT}
    ).fetchone()
    if not c:
        return None
    logger.warning('campaign.bounces.paused', campaign_id=send['campaign_id'], hard=c['hard'], sent=c['sent'])
    if c['owner_id'] is None:  # #333: no creator left to tell; the campaign page shows it paused
        return None
    return notifications.notify(
        conn,
        c['owner_id'],
        'bounces_high',
        href=f'/campaigns/{send["campaign_id"]}',
        data={'campaign': c['name'], 'invalid': c['hard'], 'attempted': c['sent'], 'paused': True},
        org_id=send['organization_id'],
    )


def recheck(campaign_id: str, org_id: str) -> None:
    """After a send: hard bounces that came while the campaign had sent fewer than ``BOUNCE_MIN_ATTEMPTS`` emails
    count once it has (3 of 15, then 15 clean sends: 3 of 30 pauses). Only a campaign with a hard bounce runs the
    count. Never raises: the send already happened."""
    try:
        with get_conn() as conn:
            if not conn.execute(
                "SELECT 1 FROM outreach_campaign_sends WHERE campaign_id = %s AND bounce_kind = 'hard' LIMIT 1",
                (campaign_id,),
            ).fetchone():
                return
            notified = _pause_above_threshold(conn, {'campaign_id': campaign_id, 'organization_id': org_id})
        notifications.deliver(notified)
    except Exception as exc:
        logger.error('campaign.bounces.recheck_failed', campaign_id=campaign_id, error_type=type(exc).__name__)


def _params(mailbox_id: str, bounce: Bounce) -> dict:
    return {
        'kind': bounce.kind,
        'status': bounce.status,
        'mailbox': mailbox_id,
        'to': bounce.recipient,
        'mid': bounce.message_id,
    }


def may_refund(mailbox_id: str, bounce: Bounce) -> bool:
    """Whether ``bounce`` is a hard one that answers a send of ``mailbox_id`` delivered within the refund window: the
    only bounce worth a paid re-verification of its address (``record``'s ``reverified_invalid``)."""
    if bounce.kind != 'hard':
        return False
    with get_conn() as conn:
        send = conn.execute(
            f'SELECT delivered_at FROM outreach_campaign_sends WHERE id = ({_SEND})', _params(mailbox_id, bounce)
        ).fetchone()
    return bool(send and send['delivered_at'] >= datetime.now(UTC) - timedelta(days=bounce_refund.REFUND_DAYS))


def record(mailbox_id: str, bounce: Bounce, *, reverified_invalid: bool = False) -> bool:
    """Apply one bounce reported to ``mailbox_id``, in its organization's scope. False when it answers no send of
    that mailbox, or that send already bounced as hard. ``reverified_invalid``: MillionVerifier, asked again at the
    bounce, says the address is invalid (owner decision on #919): only then is the credit given back. Every hard
    bounce still stops, excludes and counts toward the pause."""
    now = datetime.now(UTC)
    with get_conn() as conn:
        send = conn.execute(_MARK, _params(mailbox_id, bounce)).fetchone()
        if not send:
            # a report of no send of this mailbox, or of one already bounced hard: logged, nothing refunded
            logger.info('campaign.bounce.unmatched', mailbox_id=mailbox_id, kind=bounce.kind)
            return False
        if bounce.kind == 'soft':
            return True
        # The hard bounce path Instantly's bounces take (#931): the person stops in every campaign of the client,
        # aliases included, their waiting sends are skipped, and their Instantly leads are stopped too.
        reach = conn.execute(
            f'SELECT {TARGET_COLUMNS} FROM outreach_campaign_targets t JOIN outreach_campaigns c ON c.id = t.campaign_id'
            ' WHERE t.id = %s',
            (send['target_id'],),
        ).fetchall()
        signal = Signal(
            SignalKind.HARD_BOUNCE,
            Person(send['project_id'], email=send['recipient']),
            f'bounce:{send["id"]}',
            now,
            reach,
            stop_as='excluded',
            exclude=True,
            owner_id=send['owner_id'],
            wrote_in=[send['project_id']],
        )
        stopped = coordination.apply(conn, signal).stopped
        refunded = bounce_refund.refund(conn, send, now) if reverified_invalid else 0
        notified = _pause_above_threshold(conn, send)
    logger.info('campaign.send.bounced', send_id=send['id'], status=bounce.status, refunded=refunded)
    webhooks.sequence_stopped(stopped, 'bounced')
    notifications.deliver(notified)
    if refunded:
        balance.changed(send['organization_id'])
    return True
