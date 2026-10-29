# Campaign storage (M2)

`api/db/schema_campaigns.sql` ports the earlier implementation campaign aggregate selected in
#77. Campaigns keep ordered steps as JSONB; targets and sends are separate rows.
There is no dispatcher, endpoint or UI in this change.

Campaigns default to `draft`, approval enabled, and both active-campaign and
prior-contact duplicate guards enabled. Targets also default to `draft`, including
refill inserts: disabling campaign approval must never make an unreviewed target
sendable. Target `status` is one of `active`, `draft`, `paused`, `excluded`, `replied`;
`source` is `onboarding`, `manual`, `csv` or `refill`. Completion is represented by
`next_step` passing the last step (13 after a 12-step campaign).

Owner-composite foreign keys prevent targets or sends being attached to another
owner's campaign, or sends referring to another campaign's target. Like the rest
of this database, this schema has no RLS: future reads/writes must filter by the
authenticated owner. Campaign deletion cascades; target deletion preserves send
history with `target_id = NULL`, recipient, copy and idempotency key intact.

Nonblank email and LinkedIn identities are unique per campaign, ignoring email
case/outer whitespace and LinkedIn case/protocol/www/trailing slash/query/fragment variations.
Two email-only targets do not collide on their empty LinkedIn URL. Writers still
validate actual email/profile formats. Cross-campaign duplicate flags require
runtime checks; the indexes do not implement those policies.

Send claims default to `awaiting_approval`. The durable unique `idempotency_key`
uses `campaign:target:step`; a second unique constraint on campaign/target/step
also rejects a duplicate claim with a different key. `sending` records an attempt
with uncertain outcome; future workers must not blindly retry it. `sent` requires
`delivered_at`. Frozen body `NULL` means follow the template; `''` preserves an
approved note-less invitation. Provider-boundary approval, reply/exclusion,
kill-switch, pacing/caps and unknown-outcome checks remain required in the future
sending implementation; schema defaults alone do not authorize sending.

Every future campaign writer must call
`app.outreach.domain.campaign.validate_steps` before storing steps. It accepts
empty drafts and at most 12 steps, rejects malformed input instead of truncating
or rewriting approved text, checks contiguous numbering and ordered delays, and
supports `email`, `linkedin_connect`, `linkedin_message`, `hubspot_task` (a task step may have a
subject, never a thread). Threaded follow-up emails
need an earlier email root; LinkedIn messages need an earlier invitation and
strictly increasing delays between DMs. SQL separately enforces JSON array/size;
it deliberately does not duplicate every domain rule. the earlier implementation post reactions/comments
and manual tasks are outside this M2 port. Empty sender pools remain empty until
a writer explicitly chooses the owner's connected Google/Microsoft mailboxes;
future senders recheck connection ownership/status and the existing mailbox
`daily_limit`, `send_timezone` and `sign_off`. Campaign window days use Monday=0.

## Copy variants (#934)

A campaign tests two copies of its message when some step carries `b: {subject, body}`. Nothing else stores
that the test is on: `app.outreach.variants.has_test` reads it from `steps`. Copy B lives inside its step, so
adding, removing or moving a step carries it. Channel, delays, threading and the number of steps are shared.
That keeps every reader of the shape unchanged (`due.DUE_SQL`, the holds, `dispatcher._thread`), and it keeps the
test about copy only.

Three columns hold the rest:

- `outreach_campaigns.b_percent` (10..90 in steps of 10, default 50) is B's share of the people who join. It
  changes only in the draft: a share changed after launch would fill the gap first, and the comparison would mix
  two allocations.
- `outreach_campaigns.allocation_paused_at` is set while the allocation is paused. People who join then get copy A
  and `variant` NULL, so they stay outside the test.
- `outreach_campaign_targets.variant` (`a`, `b` or NULL) is the person's copy. NULL means copy A: no test, a join
  while paused, or a person who joined before #934.

`app.outreach.freeze.freeze` assigns the variant when it makes a complete draft active, in the same `_WRITE` that
sets `status`. It reads `b_percent`, `allocation_paused_at` and the current split from the campaign row, and
`variants.assign` gives each joiner B while B stays below `b_percent` of all the people assigned so far. Joiners go
in the order of `sha256(campaign_id:target_id)`. A running count needs one freeze of a campaign at a time, which
the project lock that every freeze caller takes gives. A per-person hash threshold needs no lock, but 29 people at
50% often split 10 to 19. The trigger `outreach_target_variant_is_final` refuses a change of `variant` except from
NULL, at `draft` to `active`, inside `freeze.freezing`. A renewal (regenerate) keeps the variant.

A joiner who may get either copy waits until both copies have their values (`variants.copy_to_fill`). With
copy A's tokens only, a B person who lacks a B-only value would go active, and each of their claims would be
refused. Everyone else needs only the values of their own copy. The dispatcher plans each step from
`variants.sequence(steps, variant)`, and a threaded B follow-up takes `Re:` of the B subject that was sent. Sends
store the rendered copy, so approval and edits need nothing new.

## Sending (#80, #81)

`app/outreach/dispatcher.py` runs every minute from Celery beat (`dispatch_campaign_sends`):

1. **Claims.** For each `active` target of an `active` campaign whose next step is due (step 1 at
   `activated_at + send_after_days`, step N at the previous step's delivery plus the difference in
   delays), it writes the send row with subject/body rendered for that person (`{{firstName}}`,
   `{{companyName}}`, …; an unknown or empty token, or copy over 300/4000 chars once rendered,
   makes the row `refused` with a reason, never truncated). With
   `approve_before_sending` (default) the row is `awaiting_approval` and nothing sends it until
   a person sets it `approved` (approval UI/API: #84). Excluded people (`app.exclusions`) become
   target `excluded`; a target without exactly one valid email skips the email step (`NO_EMAIL` /
   `INVALID_EMAIL`), and `To:` is always that single normalised address. A threaded
   follow-up takes `Re: <root subject>` and is skipped (`NO_THREAD`) if the root never went out.
   LinkedIn steps are claimed the same way (below).
2. **Delivery.** Each connected, unpaused (#89) mailbox reads the head of its own queue (no shared batch, so one
   owner's backlog or a mailbox in `error` never starves others) and sends at most one `approved`
   email per beat, from the account the person's conversation is on (`outreach_campaign_targets.sender_id`,
   pinned by the claim of the first email to an account in the campaign's pool `outreach_campaign_senders`, so a
   thread stays in one mailbox),
   inside the campaign hours/weekdays evaluated in the mailbox `send_timezone` (default
   `OUTREACH_SEND_WINDOW_TIMEZONE`), under its daily cap (`daily_limit`, default 50, warm-up 10/day
   in week one and 25/day in week two, counted from local midnight) and after a 9-14 minute gap
   since its last send. Exclusions are checked again on the recipient, and the MIME message is built
   before the claim (a header that cannot be built is `refused` `INVALID_CONTENT`). Before the provider call the
   row becomes `sending` (`attempts + 1`, `attempted_at`, `sender_provider`) in the same transaction
   that takes the mailbox's pacing slot (`user_oauth_connections.last_used_at` compare-and-set).
   Success: `sent`, `delivered_at`, `provider_receipt_id`, `thread_id`, target `next_step + 1`.
   An outcome that may have delivered (timeout after the request left, 5xx, killed worker) stays
   `sending` and is **never retried automatically**. A provider rate limit gives the attempt back and rests
   the mailbox for an hour. A definite transport/auth failure is `failed` and retried up to 3
   attempts, otherwise `refused`. A revoked grant marks the mailbox
   `error` (Settings shows "Reconnect").
3. **Message.** Approved copy + the mailbox `sign_off` + the opt-out reply footer (#75). Headers:
   minted `Message-ID`, `In-Reply-To`/`References` (+ Gmail `threadId`) on follow-ups, and, when
   `OUTREACH_REPLY_DOMAIN`/`OUTREACH_REPLY_SECRET` are set, the signed `Reply-To` plus
   `List-Unsubscribe: <mailto:reply-address?subject=unsubscribe>`. Leave those two unset until the
   inbound receiver (#86) is live: a Reply-To nobody receives would lose replies.

**Active duplicates (#83).** With `block_active_duplicates` (default on), the plan phase does not claim
any step for a person (same email, or same LinkedIn identity as the targets' unique index) who is an
`active` target, not past its last step, in another `active` campaign of the same owner that was activated
earlier. First armed wins, so two campaigns never hold each other; the hold lifts when that sequence ends,
the person replies or is excluded there, or that campaign is paused/stopped. Enrollment applies the
same rule (#148); LinkedIn "already connected" is #147.

**HubSpot read gate (#120).** No polling: HubSpot is read only when an action needs it. For an owner
with HubSpot connected, each beat first hands `app.crm.gate.check` the people a step is due for now
(`dispatcher.gate_candidates`, the plan's own "due" query); it batch-reads them and stores one verdict
per (owner, lower-cased email) in `crm_gate_checks` (`api/db/20261009083421_hubspot_gate.sql`). The plan phase
then claims a step only for a person read clear in that same beat, so a missing verdict, a failed
read, a revoked grant (connection in `error`) or `vendor_disabled:hubspot` all hold people rather than
send (fail-closed). A person a verdict blocks is not read again: a cooldown leaves the block when
`hold_until` passes and is re-read before its step; other blocks are cleared by HubSpot webhooks. After a transient failed read (5xx, 429, timeout) the beat does not retry: it queues Celery
`retry_hubspot_read` with a `countdown` (1, 2, 4… minutes up to an hour, at least HubSpot's
`Retry-After` on a 429) and leaves that owner alone until the task read HubSpot. A refused grant
(401/403) is never retried: the connection goes to `error` and its people wait until the user
reconnects, which clears their verdicts and re-evaluates them.
Rules, first match wins (`app.crm.gate.verdict`): `HUBSPOT_OPTED_OUT` (contact `hs_email_optout`, lasting);
`HUBSPOT_CUSTOMER` (the contact's primary company, `associatedcompanyid`, has `lifecyclestage` customer);
`HUBSPOT_OPEN_DEAL` (that company's `hs_num_open_deals` > 0); `HUBSPOT_OWNED` (contact or company
`hubspot_owner_id` is set and is not the connected user's own owner id, `provider_owner_id`, looked up
at connect; a user who owns nothing is blocked by any owner); `HUBSPOT_COOLDOWN` until 30 days after the
contact's `notes_last_contacted`, unless that is a touch Zenbree logged itself (write-back, #528: not later
than the person's newest `crm_activity_writes.touched_at`). Only the cooldown has a `hold_until`; the verdict keeps the HubSpot
`contact_id` / `company_id` for the webhooks.
**Webhooks (no polling).** `POST /v1/webhooks/hubspot` (`app/crm/webhooks.py`) verifies
`X-HubSpot-Signature-v3` (HMAC-SHA256 of method + public URL + body + timestamp with the app's client
secret, five minutes at most) and queues the delivery; Celery `apply_hubspot_events` runs
`gate_recheck.recheck`: for every user connected to that portal (`provider_account_id` = `portalId`) it finds
the people whose verdict rests on the changed contact or company (`contact_id` / `company_id`; a deal
event is resolved to its companies through the associations API), re-reads them and stores the whole
verdict again. That is how a block lifts or lands between two steps. A transient failed read retries the task from the queue (same countdowns, six times); a refused grant marks the connection `error` and is not retried. Connecting requires the portal id and clears the user's
stored verdicts. Subscriptions of the HubSpot app, declared in `ops/hubspot` (see its README): `contact.propertyChange`
(`notes_last_contacted`, `hs_email_optout`, `hubspot_owner_id`, `associatedcompanyid`),
`company.propertyChange` (`lifecyclestage`, `hubspot_owner_id`), `deal.propertyChange` (`dealstage`).
**Opt-out and send time.** An opt-out is for good, and it is the person's (#1121). At the start of each plan phase,
every `active`, `draft` or `paused` target of that owner becomes `excluded` (count `excluded_crm`) when it has the
opted-out address, or, in a client where a person identity holds that address, one of the identity's other addresses
or LinkedIn profiles (#937). Enrollment (create, activate, refill) treats those people like the owner's own exclusions
(`gate.opted_out_rules`, with `app.audience.follow.with_aliases`). Both read our own tables only. The send queues
(email, LinkedIn and HubSpot task) leave out any claim whose person a verdict blocks now, so a message approved days
ago does not go out after a block landed by webhook. It stays queued and is sent if the block lifts. Only an opt-out
holds a person with no email, through their aliases. The other verdicts are read and stored per email.

**HubSpot task step (#123).** A step with channel `hubspot_task` sends nothing: `app/outreach/crm_tasks.py`
claims it like any step (never `awaiting_approval`: whoever gets the task reviews the draft) and, in the same
beat, creates a HubSpot task with the rendered subject and body on the person's contact, created when HubSpot
has none, and its company. The task goes to the contact's `hubspot_owner_id`, else the company's, else the
connected user. The claim turns `sent` with the task id in `provider_receipt_id` and the person moves on. A person
with no email is skipped (`NO_EMAIL`). The read gate's `HUBSPOT_OWNED` does not hold a task step
(`TASK_FOR_OWNER` in `app/outreach/due.py`): it is how a colleague's contact reaches that colleague; every other
verdict holds it as it holds a send. A 5xx or a lost answer leaves the claim `sending` (`UNKNOWN_OUTCOME`), never
retried by itself; a refusal is `failed` and retried up to three attempts; while HubSpot cannot be read, or the
grant lacks `crm.objects.contacts.write`, the claim waits `approved`. It does not need the write-back switch.

**HubSpot write-back (#526, #527, #528).** Separate from the gate and never in a send's path: with the user's switch on, a beat sweep (`app/crm/contacts.py`) creates the HubSpot contact, and links or creates its company, of each person a send was delivered to since the switch went on. It fills only empty properties and records each person once in `crm_contact_writes`. A person the gate holds is left out. The same task then logs each delivered send and each reply on the contact's timeline (`app/crm/activities.py`, #528), once, recorded in `crm_activity_writes`.

Kill switches: `channel_disabled:email` stops delivery (claims still queue for approval);
`vendor_disabled:google` / `vendor_disabled:microsoft` stop one provider. Not done here: duplicate
and prior-contact blocking (#83), stop-on-reply (#83/#86), per-recipient LLM copy, campaign
completion.

### LinkedIn steps (#82)

Same claims, approval, exclusions and unknown-outcome rule; the copy is the rendered note/DM
(`recipient` = the target's LinkedIn URL; a target without a `/in/` profile URL is skipped
`NO_LINKEDIN`; a note over 300 characters is refused `COPY_TOO_LONG`). Delivery
(`send_linkedin_due`) goes through the owner's connected Unipile account, each with its own
head-of-queue, at most one action per account per beat: inside the campaign window **and** 9-18
Mon-Fri Europe/Rome, under 20 invitations/day, 100/week (rolling) and 30 DMs/day, after the same
9-14 minute random gap (`last_used_at` compare-and-set). Before writing anything it resolves the
profile: an invitation to someone already first-degree is skipped `ALREADY_CONNECTED` (and dates
the acceptance), a DM to someone who is not first-degree is skipped `NOT_CONNECTED`. A note longer
than the account allows (200 without Premium or when unknown) goes out note-less. A send that timed
out or answered 5xx/unreadable stays `sending`. A rate limit rests the account an hour and gives the
attempt back; LinkedIn's own limit (`OUTREACH.LINKEDIN_PROVIDER_LIMIT`) rests it 24 hours and spends
the attempt; an auth failure marks the account `error`.

Acceptance: the invited profile's Unipile id is stored on the target (`linkedin_provider_id`) and
the webhook's `new_relation` event sets `linkedin_accepted_at` on that owner's targets. A DM's delay
counts from the later of the previous step and the acceptance. Without an acceptance three days
after the invitation, the DM is skipped `NOT_ACCEPTED` and the email steps behind it go on. A DM
with no sent invitation before it is claimed at once and sent only if the profile is first-degree. The
Unipile webhook must subscribe to the `users` source (`new_relation`) with the same `Unipile-Auth`
header, or every DM falls back.

Kill switches: `channel_disabled:linkedin` and `vendor_disabled:unipile` stop LinkedIn delivery.
Not done here: LinkedIn replies stopping the sequence, retrying an invitation without its note when
LinkedIn refuses the note.

`api/db/schema_tools_sending_linkedin.sql` adds `outreach_campaign_targets.linkedin_provider_id` and
lets `outreach_campaign_sends.sender_provider` be `unipile`; apply it after
`schema_tools_sending.sql`.

`api/db/schema_tools_sending.sql` adds `message_id`, `thread_id`, `sender_provider` and
`attempted_at` to `outreach_campaign_sends`. Upgrade an existing database by applying it the same
way as below (`--file api/db/schema_tools_sending.sql`), after `schema_campaigns.sql`.

## Replies (#86, #88, #75, #83)

Email replies are read from the mailbox that sent (#1165): the mailbox's provider push
(`app/integrations/mailbox_push.py`, the one that reads bounces) lists each new message's headers, and
`app/outreach/mailbox_replies.py` records a message that answers one of our sends, when its
`In-Reply-To`/`References` names a `message_id` of that mailbox's sends or its sender is an active
target of a campaign it sends for. Only then is the message fetched whole; any other message is
never fetched and nothing of it is stored, and a delivery-status notification stays a bounce. The reply is left untouched in the
mailbox (read-only scopes). It reaches `replies.record` as the Resend route does, with the DMARC
verdict taken from the topmost `Authentication-Results` header, read only when its authserv-id is the provider's (`mx.google.com`, `*.protection.outlook.com`) and unambiguous (one `dmarc` resinfo, no quote or nested comment); the
provider's message id (`mailbox:<connection>:<id>`) or the email's own `Message-ID` makes a redelivered push, a retried
task, or the same reply arriving by Resend record once. The signed `Reply-To` exists only when
`OUTREACH_REPLY_DOMAIN` is set (optional, empty in production): mail to
`reply+<owner>.<hmac>@<OUTREACH_REPLY_DOMAIN>` is received by Resend inbound,
which posts a Svix-signed `email.received` to `POST /v1/inbound/email`; the body and headers are
read from Resend's receiving API. LinkedIn DMs come from the Unipile `messaging` webhook and are
kept only when they come from one of the owner's campaign targets (the account's other
conversations are dropped, never stored or sent to the LLM).
`app/outreach/replies.py` stores each message once in `outreach_inbound_messages` (both channels),
then:

- **Stop (#83):** a person's reply sets every matching target of that owner to `replied` and
  skips their open sends. Matching: email = sender address or `In-Reply-To`/`References` to one
  of our `message_id`s; LinkedIn = `linkedin_provider_id` or the normalised profile URL. An
  autoresponder (subject prefix / RFC 3834 headers) is recorded as `auto_reply` and stops nothing.
- **Opt-out (#75):** "no grazie" (the footer; not when it defers, e.g. "sentiamoci a gennaio"),
  an explicit unsubscribe phrase in their own words, or the `List-Unsubscribe` mailto (our minted
  subject `unsubscribe`, whatever body the client adds) is `unsubscribe`: stop + owner-wide
  exclusions for the sender and the matched targets' email/LinkedIn URL. Only the topmost
  `Authentication-Results` (the receiving server's) is read: without `dmarc=pass` there is no
  exclusion (the stop still happens); otherwise, and without the header, up to 50 opt-outs per
  owner per day (Resend's authserv-id is not pinned yet, so a pass is not fully trusted). An LLM label never creates an exclusion.
- **Labels (#88, #253):** storing a reply queues the Celery task `label_reply` for it after the
  commit (no beat). It labels the reply with the chat LLM (metered as `reply_label`); a failure is
  retried with backoff (1, 2 min), three attempts per reply in all, then it stays unlabelled. An
  answer off the label set is `other`; `interested`/`meeting_request` email the owner once via
  Resend (no reply text in the email). A reply whose task could not be queued (Redis down) is not
  labelled automatically. `PATCH /v1/inbox/{id}` sets a manual label, which the task never overwrites.

`api/db/schema_inbound.sql` creates `outreach_inbound_messages` and adds
`outreach_campaign_targets.linkedin_provider_id`; apply it to an existing database like the
files below.

### Ops to turn inbound on

Replies to a connected Gmail or Microsoft mailbox need none of this: the mailbox push of #747 (`GMAIL_PUSH_TOPIC`,
`GMAIL_PUSH_TOKEN`) carries them. The steps below add the Reply-To route, only when a domain is wanted.

1. Pick the reply subdomain, e.g. `reply.zenbree.com`. In Resend → Domains, add it with receiving
   enabled and create the MX record Resend shows (Cloudflare DNS, DNS-only).
2. Resend → Webhooks: endpoint `https://<API>/v1/inbound/email`, event `email.received`; copy
   its signing secret (`whsec_…`).
3. Prod `.env`: `OUTREACH_REPLY_DOMAIN=reply.zenbree.com`, `OUTREACH_REPLY_SECRET=<openssl rand -hex 32>`,
   `OUTREACH_INBOUND_WEBHOOK_SECRET=whsec_…`, and `RESEND_API_KEY`/`EMAIL_FROM` (already used by web).
   Setting the reply domain is what makes sends stamp `Reply-To` + `List-Unsubscribe`, so do it
   only after steps 1-2 work. Rotating `OUTREACH_REPLY_SECRET` orphans replies to mail already sent.
4. Unipile: create a second webhook (the dashboard cannot set headers): `POST /api/v1/webhooks`
   with `{"source": "messaging", "request_url": "https://<API>/v1/integrations/linkedin/webhook",
   "events": ["message_received"], "headers": [{"key": "Unipile-Auth", "value": "<UNIPILE_WEBHOOK_SECRET>"}]}`.

## Builder (#84)

`app/outreach/routes.py` (`/v1/campaigns`, contract in `docs/contracts-m1.md`) and the web pages under
`/campaigns` are the campaign writer: create a draft from a person list, edit steps while it is a draft,
pick the sender pool, preview, send a test email to yourself, activate, pause/resume, and work the
approval queue. No schema change. Not done here: changing `approve_before_sending` (always on from the
builder), editing the roster after creation, send windows in the UI, step reordering.
The builder writes copy B of a step in an A/B tab and sets `b_percent` through `PUT …/allocation`
(#934). Its A/B switch is page state: on with no copy B yet blocks activation, and off removes every `b`.

## Existing database upgrade

Compose initialization runs these files only for an empty volume. Deploying code
does **not** update an existing database. After backing up the database, apply this
single additive file from the repository root with the usual authorized database
connection environment (`PGHOST`, `PGDATABASE`, `PGUSER`, password via `.pgpass`):

```sh
psql -X --set ON_ERROR_STOP=1 --single-transaction --file api/db/schema_campaigns.sql
```

For the Compose database, run from a checkout containing the reviewed SQL:

```sh
docker compose exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < api/db/schema_campaigns.sql
```

For a remote Compose host, stream the reviewed local file over SSH (replace
`deploy@host` with the authorized deployment host); no remote SQL checkout needed:

```sh
ssh deploy@host 'cd /opt/zenbree && docker compose exec -T postgres sh -c '\''psql -X -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"'\''' < api/db/schema_campaigns.sql
```

Apply before enabling any campaign writer. Existing base/Better Auth schema must
already exist. The file creates the three tables/indexes and replaces only its
own timestamp function/trigger; replay preserves existing campaign, target, send
and unrelated application data. No table rewrite, data deletion or production
migration execution is part of this PR. `CREATE TABLE IF NOT EXISTS` supports
fresh installation/replay of this version; future column changes need explicit
ALTER migrations, not edits relying on CREATE TABLE to upgrade existing tables.

Tests load the old schema into a disposable database, retain an existing user and
credit balance, apply this file transactionally, then replay it with campaign and
reviewed target data present. Run the checks from `api/`:

```sh
TEST_DATABASE_URL=postgresql://…/disposable_test_db uv run pytest -q tests/test_campaign.py tests/test_campaign_sql.py
```
