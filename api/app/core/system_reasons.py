"""The closed list of reasons to cross organizations: a ``System`` scope runs as ``zenbree_system``, which bypasses
row-level security, so each use has a name here and a short list of modules that may enter it.

Agents: do not add a reason to make a query return rows. When code returns 0 rows under a scope, the fix is the
scope of its entry point (``acting_for`` the right organization), not a new reason. A new reason needs the user's
approval in the PR. tests/test_scope_entry_points.py pins this file's sha256: an agent never updates that hash. The
user approves each change to this file, and the PR records the approval.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class SystemReason(StrEnum):
    BACK_OFFICE = 'the back office reads every organization (require_admin)'
    NO_ORG_CLAIM = 'a token minted before #305 has no org claim: find or create the default organization'
    API_KEY_LOOKUP = 'a key hash names its organization; the key row is read before the scope exists'
    HEALTH = 'the health check pings the database for no one'
    ACCOUNT_DELETE = 'a deleted account cancels billing and releases senders in every organization it belongs to'
    PUSH_TAKEOVER = 'a browser push endpoint is one device: a subscribe takes it over from any user or organization'
    STALE_JOBS = "a new turn fails its user's dead jobs in every organization (uq_jobs_one_active_per_user spans them)"
    STRIPE_WEBHOOK = 'a Stripe event names a customer; its organization is read from the billing rows'
    INBOUND_MAIL = 'a reply address names a person or a project, not an organization (#414, #467)'
    LINKEDIN_WEBHOOK = 'a Unipile account id is matched to its connection before the organization is known'
    MAILBOX_PUSH = (
        'a Gmail push names a mailbox address, not an organization: it is matched to the connections of it (#747)'
    )
    HUBSPOT_WEBHOOK = (
        'a HubSpot portal event names a portal, not an organization: it is matched to the grants that hold it, and'
        ' beat picks the due write-backs; the reads and writes then run as the owner in their organization'
    )
    INSTANTLY_WEBHOOK = (
        'an Instantly callback names a webhook subscription Zenbree created, not an organization: the subscription'
        ' row is read to check its secret and find its organization; the facts are recorded in that organization (#930)'
    )
    AUTOMATION_WEBHOOK = (
        'a signed automation webhook names a hook id, not an organization: the hook row is read to check its secret and'
        ' find its automation; the delivery is recorded, and the run started, in that organization (#1031)'
    )
    OAUTH_CALLBACK = 'the OAuth state names the connection being completed'
    OPT_OUT = 'an opt-out is never scoped: it stops the person everywhere (#460)'
    DUE_WORK = 'beat picks due rows across organizations, then works each one in its organization, as its owner when it has one'
    RESERVATION_REAPER = 'stale credit holds are released, and settling ones committed, in every organization'
    CSV_REAPER = 'stuck CSV imports are failed, and old import files dropped, in every organization'
    DIGESTS = 'one digest per user covers every organization where they are a live member'
    RETENTION = 'old LLM usage rows are deleted for every organization'
    SHARED_GRANT_CHECK = (
        "one Google account's grant serves its rows in several organizations: before a revoke, whether another"
        ' organization still has a live row of it is read as a boolean (#471)'
    )
    BILLING_EMAIL_SYNC = "Stripe customers follow their creator's changed login email, in every organization"
    PHOTO_SWEEP = (
        "the photos bucket is swept daily: objects of users that no longer exist, or that a user's image does not name,"
        ' are deleted (#503)'
    )


#: Where each reason may be entered: module paths under api/app. Read-only, so no module widens it at run time and
#: passes the scans that read it.
ALLOWED: Mapping[SystemReason, frozenset[str]] = MappingProxyType(
    {
        SystemReason.BACK_OFFICE: frozenset({'auth/middleware.py'}),
        SystemReason.NO_ORG_CLAIM: frozenset({'auth/middleware.py'}),
        SystemReason.API_KEY_LOOKUP: frozenset({'auth/middleware.py'}),
        SystemReason.HEALTH: frozenset({'main.py'}),
        SystemReason.ACCOUNT_DELETE: frozenset({'auth/account_delete.py'}),
        SystemReason.PUSH_TAKEOVER: frozenset({'notifications/routes.py'}),
        SystemReason.STALE_JOBS: frozenset({'agent/routes.py'}),
        SystemReason.STRIPE_WEBHOOK: frozenset({'billing/routes.py'}),
        SystemReason.INBOUND_MAIL: frozenset({'outreach/inbox_routes.py'}),
        SystemReason.LINKEDIN_WEBHOOK: frozenset({'linkedin/api.py'}),
        SystemReason.MAILBOX_PUSH: frozenset({'integrations/mailbox_push.py'}),
        SystemReason.HUBSPOT_WEBHOOK: frozenset({'crm/webhooks.py', 'worker.py'}),
        SystemReason.INSTANTLY_WEBHOOK: frozenset({'integrations/instantly_sync/webhook.py'}),
        SystemReason.AUTOMATION_WEBHOOK: frozenset({'automations/inbound.py'}),
        SystemReason.OAUTH_CALLBACK: frozenset({'integrations/mailboxes.py', 'integrations/hubspot.py'}),
        SystemReason.OPT_OUT: frozenset(
            {'outreach/unsubscribe_routes.py', 'outreach/replies.py', 'outreach/campaign_beat.py'}
        ),
        SystemReason.DUE_WORK: frozenset({'worker.py'}),
        SystemReason.RESERVATION_REAPER: frozenset({'worker.py'}),
        SystemReason.CSV_REAPER: frozenset({'worker.py'}),
        SystemReason.DIGESTS: frozenset({'worker.py'}),
        SystemReason.RETENTION: frozenset({'worker.py'}),
        SystemReason.SHARED_GRANT_CHECK: frozenset({'integrations/member_release.py'}),
        SystemReason.BILLING_EMAIL_SYNC: frozenset({'worker.py'}),
        SystemReason.PHOTO_SWEEP: frozenset({'auth/photo_sweep.py'}),
    }
)
