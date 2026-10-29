"""The hourly digest run (app/worker.py ``send_notification_digests``): take the digests due this hour and send each."""

import time
from datetime import UTC, datetime

from app.core.db import get_conn
from app.notifications.service import send_digest, take_digests


def run() -> dict:
    with get_conn() as conn:
        batches = take_digests(conn, datetime.now(UTC))
    sent = 0
    for i, digest in enumerate(batches):
        if i:
            time.sleep(0.6)  # under Resend's 2 requests/s
        sent += send_digest(digest)
    return {'sent': sent}
