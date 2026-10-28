# API keys

An admin of the organization creates a personal `sbk_` API key on Settings › API key; a member sees no `API key` tab. The plaintext key is shown once, with `button "Copia"`, and the user calls the API with it. Revoking the key makes the API reject it.

## Sub-features

- `keys-create` creates a labelled key and shows its plaintext once.
- `keys-use` authenticates API calls with `Authorization: Bearer sbk_…`.
- `keys-list` lists keys in a table: name, `Chiave` (the mask `sbk_…4f2a`, the last 4 characters of the plaintext; `Non disponibile` for a key issued before they were stored), created date (`3 ott 2026`), and an `Attiva` or `Revocata` status.
- `keys-revoke` revokes a key; after that the API returns 401.
- `keys-banned` (#360): while the key's creator is banned (back office `Banna`, [admin-users](admin-users.md)), the key gets 401 `AUTH.API_KEY_INVALID` and a JWT minted before the ban gets 401 `AUTH.NOT_A_MEMBER`; `Rimuovi ban`, or a `banExpires` in the past, makes both work again. The key is not revoked.
- `keys-docs` (#349): `link "Documentazione API"` after the credits note opens `$VERIFY_API/docs` (FastAPI's Swagger UI) in a new tab.

## How to get to it (user POV)

- Choose `link "Impostazioni"` in the sidebar, then the tab `link "API key"` in `navigation "Impostazioni"`. It opens `/settings/api-keys`.
- Use the key from any HTTP client: `curl -H "Authorization: Bearer <key>" "$VERIFY_API/v1/me"`.

## Driving it with bin/browser

Preconditions:

- Seed and sign in a user, and get past onboarding:
  1. `bin/stack seed-user keys@verify.local verify-pass-1234`
  2. Sign in from the browser, as described in the README.
  3. `bin/browser click button "Salta per ora"`
- The user has fewer than 10 active keys.

- **Open the page.** Run `bin/browser click link "Impostazioni"`, `bin/browser click link "API key"`, then `bin/browser wait-url /settings/api-keys`. A new user sees `Non hai ancora creato API key.`
- **Create.**
  1. `bin/browser fill textbox "Nome della chiave" "verify curl"`
  2. `bin/browser click button "Crea chiave"`
  3. `bin/browser wait textbox "Nuova API key"`

  A `status` region contains `Chiave creata`, and the key list gains `verify curl` with `button "Revoca la chiave verify curl"`.
- **Read the key.** Run `K=$(bin/browser eval "document.querySelector('input[aria-label=\"Nuova API key\"]').value" | tr -d '"')`. `${K:0:4}` is `sbk_`.
- **Use.** Run `curl -sS -w ' %{http_code}\n' -H "Authorization: Bearer $K" "$VERIFY_API/v1/me"`. The status is `200` and the body is `{"user_id":…,"email":null,"role":null,"org_role":"admin","credits_remaining":<balance>,"plan_credits":…,"plan_credits_left":…,"plan_credits_reset_at":…,"purchased_credits":…}`.
- **Read the shapes.** `$VERIFY_API/docs` (Swagger) and `$VERIFY_API/openapi.json` need no token. Every JSON response has a schema (#369), e.g. `paths["/v1/lists"].get.responses["200"]` is `{"$ref": "#/components/schemas/Lists"}`, and the SSE routes list only `text/event-stream`.
- **Revoke.**
  1. `bin/browser click button "Revoca la chiave verify curl"` opens `alertdialog "Revocare la chiave «verify curl»?"` with `button "Annulla"` and `button "Revoca chiave"`. The dialog lists two consequences.
  2. `bin/browser click button "Revoca chiave"`, then `bin/browser wait-gone button "Revoca la chiave verify curl"`. A toast reads `Chiave «verify curl» revocata.`

  The row's status cell now reads `Revocata`. The same curl now returns `401` with `{"detail":{"code":"AUTH.API_KEY_INVALID","message":"Invalid or revoked API key"}}`.
- **Proof.**
  1. Save `bin/browser ax --out "$VERIFY_ARTIFACTS/api-keys/created.ax.txt"`, and redact the `sbk_` value.
  2. Save both curl outputs with their status codes.
  3. Save `bin/stack psql -tAc "SELECT label, revoked_at IS NOT NULL FROM user_api_keys"`, which prints `verify curl|t`.

## Gotchas

- The plaintext key appears only once, in the readonly `textbox "Nuova API key"`. Read it before you navigate away. The database stores only a hash.
- The `ax` dump contains the plaintext key as the textbox value. Redact it before you share the artifact.
- `/v1/me` with an API key returns `email: null` and `role: null`. That is expected: keys carry no role. Admin routes take a JWT only, so a key gets 401 `AUTH.TOKEN_INVALID` there, not 403.
- `button "Crea chiave"` is disabled while the label is empty.
- Revoke asks for confirmation in an `alertdialog`. `Annulla` keeps the key active.
- Driven 2026-10-04 at 9c0ac03.
- Driven 2026-10-07 at 40a7ebc: `keys-docs` href, `target=_blank`, and `/docs` answering 200.
- Driven 2026-10-07 on #369: keys-use, and the response schemas in `/openapi.json`.
- Driven 2026-10-07 on fix/360-ban-api-keys: `keys-banned` (ban and `Rimuovi ban` from the back office; the timed ban set with SQL, since the dialog has no expiry field).
- Driven 2026-10-10 at dae0d57: create, copy, use, list with the `Chiave` mask, revoke and the 401 after it.
- Proof of `button "Copia"` needs the real clipboard. Headless `bin/browser` has none. Click it in the MCP Chrome, then run `pbpaste` and compare the value with the key on the page (redact it in the report). A rejected clipboard shows the `copyFailed` alert, and only a unit test reaches that path.
