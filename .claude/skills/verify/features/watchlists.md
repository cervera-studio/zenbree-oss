# Watchlists and signals

A watchlist watches the accounts of a company list for new job postings in chosen functions and records each one once as a signal with a score that decays (E13.3, #647). There is no page yet (#649): the feature is the API `/v1/watchlists` and two Celery tasks.

## Sub-features

- `watch-create` makes a watchlist from a company list of the project; a person list or another project's list is refused.
- `watch-run` reads each account's postings on the beat or on `POST …/run`, charges one credit per account that has postings, and records the new postings in the watched functions.
- `watch-signals` lists the signals with their current score.
- `watch-rotation` skips an account with no domain or one the vendor does not know, and keeps its place in the rotation.
- `watch-failure` fails a run when the vendor gives up, keeping and charging what was read.
- `watch-pause` pauses, resumes and deletes.

## How to get to it (user POV)

Any HTTP client with an `sbk_` key ([api-keys](api-keys.md)) or a session token: `curl -H "Authorization: Bearer <key>" "$VERIFY_API/v1/watchlists"`.

## Driving it

Preconditions:

- No Apollo key is needed: `bin/fake-apollo > apollo.log &` answers the two organization endpoints on `:18767`. Before `bin/stack up`, `export APOLLO_API_KEY=verify-key APOLLO_BASE_URL=http://host.docker.internal:18767`. Organization id `hiring` has three postings (`Senior SDR` today, `SDR Italia` 30 days old, `Backend engineer`) and a fourth, `SDR Team Lead`, after `curl localhost:18767/more`; `quiet` has none; `broken` answers 500; only the domain `hiring.verify.test` is known.
- An API key for the seeded user: its hash from `bin/stack compose exec -T api python -c "from app.auth.middleware import hash_api_key; print(hash_api_key('sbk_…'))"`, inserted into `user_api_keys` with `default_organization(<user id>)`.
- A company list in the user's project (`INSERT INTO lists … entity 'company'`) with rows whose `cells` are `{"name":"Hiring Srl","domain":"hiring.verify.test","apollo_id":"hiring"}`, `{"name":"Quiet Spa","apollo_id":"quiet"}`, `{"name":"By Domain","domain":"hiring.verify.test"}`, `{"name":"No Domain"}` and `{"name":"Ghost","domain":"ghost.verify.test"}`. The user needs at least 5 credits.

- **Create.** `POST /v1/watchlists {"name":"Assunzioni sales","list_id":"<list>","job_titles":["SDR"]}` answers 201 with `accounts: 5`, `cadence: "weekly"`, `enabled: true`. The same body with a person list's id answers 404 `WATCHLIST.LIST_NOT_FOUND`. Without a key: 401.
- **First run.** The watchlist is due at once: within a minute the beat (`dispatch_watchlists`) queues a run and `run_watchlist` does it. `GET /v1/watchlists/<id>/runs` shows `status: succeeded`, `credits_spent: 2`, `accounts_checked: 3`, `accounts_skipped: 2`, `accounts_left: 0`, `signals_new: 4`. The user's `credits_remaining` drops by 2 (the two accounts that have postings; `Quiet Spa` is free), and `credit_transactions` has a `deduction` of 5 with `reference_type 'watchlist'` (`settlement_net_credits` 2) and a `refund` of 3 with `reference_type 'reservation.slack'`. `row_charges` has one record of 1 credit for each account with postings (`Hiring Srl`, `By Domain`), each naming that deduction as its `hold_id` (#776).
- **Signals.** `GET …/signals` lists four, strongest first: `Senior SDR` at `100.0` and `SDR Italia` at `50.0`, for `Hiring Srl` and for `By Domain`. `Backend engineer` is not there.
- **Rotation.** `SELECT r.cells->>'name', a.vendor_id, a.skip_reason FROM watchlist_accounts a JOIN list_rows r ON r.id = a.row_id` shows `By Domain|hiring|` (resolved from its domain), `Ghost||not_found`, `No Domain||no_domain`. `apollo.log` has one `GET /organizations/enrich?domain=hiring.verify.test`; a second run adds none.
- **A new posting.** `curl localhost:18767/more`, then `POST …/run` (202): the next run shows `signals_new: 2` (`SDR Team Lead` for the two accounts) and `GET …/signals` six.
- **Vendor failure.** `UPDATE list_rows SET cells = cells || '{"apollo_id":"broken"}' WHERE cells->>'name' = 'Quiet Spa'`, delete that row's `watchlist_accounts` row, `POST …/run`: the run is `failed` with `error_code: vendor_failed`, `credits_spent: 2`, and the signals are still six.
- **Pause and delete.** `PATCH … {"enabled":false}` answers `enabled: false`; `POST …/run` then answers 409 `WATCHLIST.PAUSED`. `DELETE` answers 204 and `SELECT count(*) FROM signals` is 0.
- **Signal to campaign (#648).** Setup: a person list `Persone` in the same project with `Ada Rossi` and `Bob Neri` (`company_domain hiring.verify.test`), `Quinn Quiet` (`company_name Quiet Spa`), and a draft campaign from another one-person list (`POST /v1/campaigns`, session token). `POST /v1/watchlists` with `campaign_id` and no `campaign_threshold` answers 422; with `campaign_threshold: 60` it answers 201 with both. The first run (`Senior SDR` 100 + `SDR Italia` 50 for `Hiring Srl`) shows `people_enrolled: 2`, `accounts_without_people: 0`; `GET /v1/campaigns/<id>/targets` has Ada and Bob as `draft` with `signal_title: "Senior SDR"` and Quinn absent; `outreach_campaign_targets.source` is `signal` for them. A second run adds nobody. In an active campaign (setup: `PATCH` two email `steps`, then `UPDATE outreach_campaigns SET status = 'active'`; without steps the beat completes it) the two enter `active` and within a minute the dispatcher has an `awaiting_approval` send for each, the page showing `To approve`. With the campaign set to `paused` (setup), `/campaigns/<id>` shows `Entered because: Senior SDR` under each of the two (`Entrata per: …` in Italian).
- **Proof.** Save the JSON answers under `$VERIFY_ARTIFACTS/watchlists/` and `apollo.log`.

Driven 2026-10-12 with the fake vendor, every step above. Not driven: a real Apollo key, the credit cap of 100 and a list of more than 300 accounts (SQL tests in `api/tests/test_watchlists_sql.py` cover the cap with a lowered one), a run without credits, a worker killed mid-run.

Driven 2026-10-16 on #648 (fake vendor): the signal-to-campaign step above, in English and Italian, and again with an active campaign. Not driven: a campaign that ended, an account with no known person (SQL test), a rule-excluded person (SQL test).
