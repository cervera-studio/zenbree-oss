# Public API

Use the API service base URL (the local Compose default is `http://localhost:8000`).
Interactive OpenAPI documentation is available at `/docs`, with the schema at
`/openapi.json`. In **Authorize**, enter your API key (`sbk_…`) or Better Auth JWT.
All run and list endpoints accept `Authorization: Bearer <credential>` and only
expose the authenticated user's resources.

## Start and poll a run

```bash
curl "$ZENBREE_API_URL/v1/runs" \
  -H "Authorization: Bearer $ZENBREE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Find marketing agencies in Milan"}'
```

`POST /v1/runs` returns HTTP **202**, without an SSE connection:

```json
{"id":"<run UUID>","chat_session_id":"<session UUID>"}
```

The message must contain 1–20,000 characters, unless the request sends `segment`. Optional `project_id` assigns a new
session to an owned project; omit it for the default project. Optional `session_id`
continues an owned session and its lists; `project_id` is ignored for a continuation.
Optional `goal: "campaign"` makes a new session a campaign chat: the agent builds a list
of people with emails and creates a campaign draft from it (nothing is sent). Like
`project_id`, it is read only when the request creates the session.
Optional `locale`, one of the interface languages (any other value is 422), is the
language the agent writes in for this run. It is never saved on the user. Without it,
the agent uses the language saved on the user.
Optional `segment`, in place of `message`, is the `key` of an onboarding segment stored on
the project, as `GET /v1/home/welcome` lists it. The run then starts that segment's first
list, with the message, title and search brief the server writes. A request with both
`message` and `segment` is 422. `segment` is read only when the request creates the
session: with `session_id` it is 422. A key that no stored segment has is 422
`CHAT.SEGMENT_GONE`.
A session is created internally so the same agent, history and list persistence
are used as in chat; clients do not need to open the chat UI or consume SSE.

Poll `GET /v1/runs/<run UUID>` with the same authorization header. It returns the
same object as `GET /v1/jobs/<run UUID>`:

```json
{
  "id":"<run UUID>", "chat_session_id":"<session UUID>",
  "status":"queued", "phase":null,
  "progress":{"done":0,"total":0}, "list_id":null,
  "credits":{"reserved":0,"spent":0}, "error":null,
  "created_at":"2026-10-01T12:00:00Z", "finished_at":null
}
```

Stop polling on `needs_input`, `succeeded`, `failed` or `cancelled`. `needs_input`
means the agent asked a question (the last assistant message of the session); answer
it with another `POST /v1/runs` carrying the same `session_id`. A run estimated above
100 credits first stops on a plan instead: the session's last assistant message has
`question.plan` (`list_id`, `version`, `estimated_credits`, …). Approve it with
`POST /v1/runs` `{"message": "...", "session_id": "...", "plan_approval": {"list_id": "...",
"version": 1, "rows": 200}}`; that run may spend up to the plan's estimate for `rows`.
`phase` identifies the current tool or scoring step; `progress.total` is zero (unknown)
unless the run executes a plan, whose `stages` then report progress.
Acceptance means the job was queued, not that its results are ready. Credits are
reserved and charged by the existing worker under the same rules as chat.

Once `list_id` is present, use existing endpoints:

- `GET /v1/lists/<list UUID>` for metadata and columns.
- `GET /v1/lists/<list UUID>/rows?limit=100` for rows; pass `next_cursor` as `cursor`
  until it is null. Maximum page size is 500. `cells` has no `email_status`, `phone_status` or `source`:
  read the email and phone state from each row's `lookups`, and the provider of a cell from `cell_meta`.
- `GET /v1/lists/<list UUID>/export.csv` for CSV.
- `GET /v1/lists` to discover all owned lists, including when a session produces
  multiple lists; the run's `list_id` points to its last touched list.

## Webhooks instead of polling

Register an https endpoint and receive signed events: `list.completed` (a run added rows to a
list), `contact.replied`, `campaign.finished` and `sequence.stopped`. Endpoints belong to the organization:
only an admin's key manages them, and they receive the events of every member.

```bash
curl "$ZENBREE_API_URL/v1/integrations/webhooks" \
  -H "Authorization: Bearer $ZENBREE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/zenbree","label":"CRM","event_names":["list.completed"]}'
```

The response carries the signing `secret` (`whsec_…`) once. Deliveries follow
[Standard Webhooks](https://www.standardwebhooks.com), so its libraries verify them:

```python
from standardwebhooks.webhooks import Webhook

event = Webhook(secret).verify(raw_body, headers)  # raises on a bad signature or a stale timestamp
```

`POST /v1/integrations/webhooks/<id>/test` sends a `webhook.test` event now and returns the
endpoint's status. Payloads, retries and limits: `docs/contracts-m1.md`, section Webhooks.

## Errors

| HTTP | Meaning |
| --- | --- |
| 401 | Missing, invalid or revoked credentials. |
| 403 | `JOB.NOT_OWNER`: stopping a run another member started (only they or an admin can); `AUTH.ORG_ADMIN_ONLY`: an admin-only route (webhooks) with a member's key. |
| 404 | Run, project or session missing or in another organization. |
| 409 | `CHAT.JOB_RUNNING`: one queued/running job per user and one per chat (from any member), shared with chat. Poll the existing run before starting another. |
| 422 | Invalid request, including missing/empty/oversized message or malformed UUID. |
| 503 | `CHAT.QUEUE_UNAVAILABLE`: enqueue failed. The job is marked failed, a session the request opened is deleted, and a later request may retry. |

Application errors use `{"detail":{"code":"…","message":"…"}}`; validation
errors use FastAPI's standard `detail` array. There is no idempotency key: after
an ambiguous network failure, check your sessions/jobs before submitting again.
