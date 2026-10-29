"""The hook of a webhook automation (#1031): its public URL and its signing secret, in ``automation_webhooks``.

The URL names a random hook id, never the automation. The secret is a Standard Webhooks ``whsec_`` key, stored
encrypted (``token_crypto``) because checking a signature needs the key itself. It is shown once, in the answer that
makes it; a new one replaces it at once, and the URL stays.
"""

from __future__ import annotations

from app.automations.discard import WAITING_FOR_APPROVAL
from app.core.config import settings
from app.core.errors import is_err
from app.core.errors_http import api_error
from app.integrations import token_crypto
from app.integrations.webhooks import new_secret


def url(hook_id) -> str | None:
    base = settings.api_public_base_url.rstrip('/')
    return f'{base}/v1/automation-hooks/{hook_id}' if base else None


def rotate(conn, automation: dict) -> dict:
    """A new secret, as ``WebhookSecret``. The caller holds the automation's lock. The old secret fails from this
    commit; the hook id, so the URL, is kept."""
    if automation['trigger_kind'] != 'webhook':
        raise api_error(409, 'AUTOMATION.NOT_WEBHOOK', 'switch the trigger to webhook first')
    secret = new_secret()
    sealed = token_crypto.encrypt(secret)
    if is_err(sealed):
        raise api_error(503, 'AUTOMATION.WEBHOOK_UNAVAILABLE', 'webhooks are not available now')
    row = conn.execute(
        'INSERT INTO automation_webhooks (automation_id, organization_id, secret_ciphertext) VALUES (%s, %s, %s)'
        ' ON CONFLICT (automation_id) DO UPDATE SET secret_ciphertext = EXCLUDED.secret_ciphertext, rotated_at = now()'
        ' RETURNING hook_id, rotated_at',
        (automation['id'], automation['organization_id'], sealed.value),
    ).fetchone()
    return {'url': url(row['hook_id']), 'secret': secret, 'rotated_at': row['rotated_at']}


def remove(conn, automation_id) -> None:
    """The trigger left ``webhook``: the URL answers 401 from this commit, and a switch back makes a new one."""
    conn.execute('DELETE FROM automation_webhooks WHERE automation_id = %s', (automation_id,))


def summaries(conn, automation_ids: list) -> dict:
    """``AutomationWebhook`` of each automation that has a hook, by automation id."""
    rows = conn.execute(
        'SELECT w.automation_id, w.hook_id, w.rotated_at, (SELECT count(*) FROM automation_deliveries d'
        " WHERE d.automation_id = w.automation_id AND d.state = 'pending') AS pending,"
        f' {WAITING_FOR_APPROVAL} AS waiting_for_approval'
        ' FROM automation_webhooks w JOIN automations a ON a.id = w.automation_id WHERE w.automation_id = ANY(%s)',
        (automation_ids,),
    ).fetchall()
    return {
        r['automation_id']: {
            'url': url(r['hook_id']),
            'rotated_at': r['rotated_at'],
            'pending': r['pending'],
            'waiting_for_approval': r['waiting_for_approval'],
        }
        for r in rows
    }
