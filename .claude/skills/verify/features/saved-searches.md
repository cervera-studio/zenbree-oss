# Saved searches

A saved search reruns a `search_people` or `search_companies` call on a list, on a daily or weekly cadence, and adds the new rows. There is no page yet: the feature is the API `/v1/saved-searches` and the beat task `dispatch_saved_searches`. Sharing between members is in [team](team.md), a client's credit cap in [clients](clients.md).

## Sub-features

- `saved-create` saves a search on a list of the project.
- `saved-run` runs it on the beat or on `POST …/run`, and lists the runs.
- `saved-free` runs a free search with no hold (#776): `search_people` and `search_companies` have no `Price`, so a run takes no credit, also with an empty wallet or a project at its hard cap.

## How to get to it (user POV)

Any HTTP client with an `sbk_` key ([api-keys](api-keys.md)) or a session token: `curl -H "Authorization: Bearer <key>" "$VERIFY_API/v1/saved-searches"`.

## Driving it

Preconditions:

- A people search needs Apollo. `bin/fake-apollo > apollo.log &` answers `POST /mixed_people/api_search` on `:18767` with three people of `Hiring Srl` (`Anna`, `Bruno`, `Carla`), whatever the filters. Before `bin/stack up`, `export APOLLO_API_KEY=verify-key APOLLO_BASE_URL=http://host.docker.internal:18767`. On a running stack, recreate api and worker with the same exports (`bin/stack compose up -d api worker`). Stop the fake after the run.
- A user from `bin/stack seed-user`, an API key inserted as in [watchlists](watchlists.md), and one `GET /v1/projects` with that key: the first call creates the default project. Then, as setup, a person list in that project (`INSERT INTO lists … entity 'person'`) and a wallet of 0 (`UPDATE user_credits SET credits_remaining = 0 WHERE organization_id = <org>`).

- **Create.** `POST /v1/saved-searches {"name":"Head of sales","tool_name":"search_people","tool_input":{"titles":["Head of Sales"]},"list_id":"<list>"}` answers 201. With `"enabled": false` it is paused: `POST …/run` answers 409 `SAVED_SEARCH.PAUSED`. `PATCH … {"enabled":true}` resumes it. The body takes no `next_run_at` on `PATCH` (422 `REQUEST.INVALID_INPUT`).
- **Free run with a wallet of 0.** `POST …/run` answers 202 with a `queued` run. A few seconds later `GET …/runs` shows `status: succeeded`, `reservation_id: null`, `credits_spent: 0` and the new row ids; the list has the rows (`Anna`, `Bruno`, `Carla`) and `apollo.log` one `POST /mixed_people/api_search`. The organization has no `credit_transactions` row and no `row_charges` record, and the wallet stays 0.
- **Proof.** Save the JSON answers and a `psql` read of `credit_transactions` and `row_charges` for the organization under `$VERIFY_ARTIFACTS/saved-searches/`.

## Gotchas

- Driven 2026-10-14 on the credit-pricing stack (#776, at #778's head) with the Apollo stand-in: create, paused 409, resume, a free run at 0 credits. Not driven: a paid search (no real search has a `Price`; `api/tests/test_scheduling_sql.py` covers the quote, the cut at the quote and the refusal of a price over 1 credit), a hard cap, a campaign refill, a worker killed mid-run.
- Driven 2026-10-16 at c2551a38 against `bin/fake-apollo`: create paused (409 `SAVED_SEARCH.PAUSED`), resume, and a free run at a wallet of 0 that wrote three rows, no `credit_transactions` and no `row_charges`. The run took about 15 s to leave `queued`.
- Driven 2026-10-16 (#961): with the organization's `free_rows:<org>:<UTC date>` Redis key at 1000 and a wallet of 0, a run failed `over_budget` with no Apollo call; with the key at 10 the run succeeded (three rows, one Apollo call) and the key read 13.
