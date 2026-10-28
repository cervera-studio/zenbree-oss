# Audience identity

One person or account that several list rows stand for gets one identity in its project, so an opt-out or a duplicate check follows every exact alias we know (#937). There is no page yet: the feature is `/v1/audience` and the rules that `load_rules` and enrollment apply.

## Sub-features

- `audience-link` links rows of two lists that share an exact email, LinkedIn URL or company domain to one identity, and never links on a name.
- `audience-row` reads a row's aliases with their source and time (`GET /v1/audience/rows/<id>`).
- `audience-conflict` raises a conflict when one row ties two identities, and merges nothing.
- `audience-resolve` merges a conflict or keeps its identities apart.
- `audience-optout` makes an opt-out of one alias exclude the others, in that project only.
- `audience-enroll` makes enrollment treat a person under two aliases as one.
- `audience-scope` returns 404 for a row or conflict of another project or organization.

## How to get to it (user POV)

Any HTTP client with an `sbk_` key ([api-keys](api-keys.md)) or a session token: `curl -H "Authorization: Bearer <key>" "$VERIFY_API/v1/audience/conflicts"`. Rows reach it through a CSV upload, a chat search or an enrichment; the exclusion shows on `/settings/exclusions` and in a campaign's draft.

## Driving it

Preconditions:

- `bin/stack up` with `VERIFY_ID` set, and an API key for `dev@zenbree.local` as in [api-keys](api-keys.md) (`K` below).
- Two person lists made by CSV upload, the live path that `add_rows` takes. List `Uno`: `curl -sS -X POST -H "Authorization: Bearer $K" --data-binary @uno.csv "$VERIFY_API/v1/lists/upload?entity=person&name=Uno"` with `uno.csv` holding the header `full_name,email` and the rows `Ada Rossi,ada@acme.verify.test`, `Sam Name,sam@acme.verify.test`, `No Alias,`. List `Due`: the same call with `due.csv`, header `full_name,email,linkedin_url`, rows `Ada R.,ADA@acme.verify.test,https://www.linkedin.com/in/ada-rossi/` and `Sam Name,,`. Wait for each import to end (`GET /v1/lists/imports/<id>` says `done`), then for one beat (30 s) of `link_audience_rows`: `SELECT count(*) FROM audience_link_queue` is `0` when every row is linked.

- **Link.** `bin/stack psql -tAc "SELECT count(*) FROM audience_identities"` gives `2` (Ada, Sam). `SELECT r.cells->>'full_name', k.identity_id FROM list_rows r LEFT JOIN audience_row_links k ON k.list_row_id = r.id` shows `Ada Rossi` and `Ada R.` on one identity, `Sam Name` of `Uno` on another, and `No Alias` and the `Sam Name` of `Due` (same name, no alias) with no identity. The list rows stay 5: nothing merged into one row.
- **Row.** `GET /v1/audience/rows/<id of Ada R.>` lists two aliases, `email ada@acme.verify.test` and `linkedin_url linkedin.com/in/ada-rossi`, each with its `source` and `observed_at`.
- **Opt-out follows the alias.** `POST /v1/exclusions {"items":[{"kind":"email","value":"ada@acme.verify.test"}]}`. Upload a third list whose only row has `linkedin_url` `https://linkedin.com/in/ada-rossi`: the import drops it as excluded (`GET /v1/lists/imports/<id>/discarded.csv` holds it). `DELETE` the exclusion and the same upload is no longer `excluded`: it is `in_lists`, because `Due` already holds that LinkedIn URL as its dedupe key.
- **Conflict.** No screen edits a cell (the web PATCHes only `excluded`), so call `PATCH /v1/lists/<Due id>/rows/<Ada R. id> {"cells":{"email":"sam@acme.verify.test"}}` with the session token or the key, and wait one beat. `GET /v1/audience/conflicts` lists one open conflict with two identities and an `evidence` that names both sources, and the identities stay two. A row with only `info@acme.verify.test` gets no identity.
- **Resolve.** `POST /v1/audience/conflicts/<id>/resolve {"action":"merge"}` answers `merged` with the older identity. `GET /v1/audience/conflicts` is then empty, `?status=merged` lists it, and a second resolve answers 409 `AUDIENCE.CONFLICT_CLOSED`. Make a second conflict the same way: `{"action":"keep_apart"}` answers `kept_apart`, and `audience_link_row` does not raise it again.
- **Same LinkedIn, two emails.** An upload drops a row whose LinkedIn URL another list of the project holds (`in_lists`: it is the person dedupe key), so this case comes from a hand edit. Upload a list with `Ada Due` and no email or LinkedIn (no alias, so no identity), then `PATCH` its row with `{"cells":{"email":"ada.due@other.verify.test","linkedin_url":"https://linkedin.com/in/ada-rossi?trk=x"}}` in one call. Within one beat `audience_row_links.created_at` of the row is at most 30 s after the mark's `queued_at`, on Ada's identity. Patch both values in one call before the first link: a row the beat already linked to its own identity becomes a conflict when it gains another identity's alias.
- **Enrollment.** With an exclusion of `ada@acme.verify.test` (Settings › Esclusioni), create a draft from the list that holds the other row (`Nuova campagna da una lista`): the builder reads `<n> persone da <list> · esclusioni già tolte` and `outreach_campaign_targets` has no row for her other email. Remove the exclusion and a second draft holds her. A reply follows the alias too: mark her target of a draft from `Uno` `replied` (setup SQL), and a new draft from the other list skips her other row. A person row whose `linkedin_url` is a company page (`/company/acme`) or a bare `linkedin.com/in` joins nobody: an exclusion of one such row's email leaves another row with the same URL in the draft.
- **Own cells before the link.** `bin/stack compose stop beat`, upload a list, and check that its rows have a mark in `audience_link_queue` and no `audience_row_links`. Add exclusions of one row's email, another's LinkedIn URL and a third's domain, and create a draft from the list: only the fourth row is a target. `bin/stack compose start beat` links them.
- **Scope.** `GET /v1/audience/rows/<id of a row of another client>` answers 404 `AUDIENCE.ROW_NOT_FOUND`, and so does this client's row with another project's `X-Project-Id`. Resolving another client's conflict answers 404 `AUDIENCE.CONFLICT_NOT_FOUND`.
- **Proof.** Save the JSON answers and the psql output under `$VERIFY_ARTIFACTS/audience/`, with `sbk_` keys redacted.

## Gotchas

- A row has no identity until the beat links it, up to 30 s after the write: a check that runs sooner still matches the row's own email, LinkedIn URL and domain, and only the other aliases wait. A row written by raw SQL (the dev seed) is not marked: `SELECT audience_link_row(id) FROM list_rows` links it. The migration did that for the rows that existed then. A role address (`info@`, `sales@`) is never an alias.
- A conflict never merges by itself, and an alias that a conflict leaves out is attached to neither identity.
- Not driven in the first PR: a real HubSpot or Instantly id (#923 and #929 call `attach_alias`). `api/tests/test_audience_sql.py` covers them with stubs.
- Driven 2026-10-16 on #956 (6aa7be2c): link, row, conflict, merge and keep apart, enrollment with an exclusion and a reply, own cells with the beat stopped, scope across organizations and projects.
