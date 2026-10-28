# Admin settings

An admin opens the back office at `/admin`. On Impostazioni they manage runtime switches: `vendor_disabled:<provider>` and `channel_disabled:<channel>`. A switch that is on blocks that vendor's tools: at once for the chat tools, within 30 s for outreach sends. Users who are not admins get an access-denied page.

## Sub-features

- `admin-gate` shows the 403 page ("Questa pagina è riservata al team Zenbree") to a non-admin and the back office to an admin.
- `admin-settings-add` creates a switch key through `POST /v1/admin/settings`. A new key starts off (`false`). An existing key (409 `ADMIN.SETTING_EXISTS`) or one that is not `vendor_disabled:<name>`/`channel_disabled:<name>` (422 `ADMIN.INVALID_SETTING_KEY`) changes nothing and shows an inline error under the field (#338).
- `admin-settings-toggle` flips a switch after an `alertdialog` confirm (production: `Accendere <key>?` / `Spegnere <key>?`) and persists it. A switch that is on shows `Bloccato`; non-boolean keys show their value and `sola lettura`.
- `admin-api` reads `GET "$VERIFY_API/v1/admin/settings"`. It accepts an admin JWT only: a user JWT gets 403 `AUTH.FORBIDDEN`, an `sbk_` key gets 401.

## How to get to it (user POV)

- Click `button "Menu account"` in the sidebar foot and choose `menuitem "Backoffice admin"` (the name includes the "admin" badge). Only admins see it.
- In the back office, use the admin nav: Utenti, Organizzazioni, Costi, Job, Impostazioni, and "Torna all'app".

## Driving it with bin/browser

Preconditions:

- Sign in a user from `bin/stack seed-user` who is past onboarding, as described in the README. Not `dev@zenbree.local`: the dev seed makes it an admin.
- The `settings` table has no `vendor_disabled:apollo` row.

- **Gate, non-admin.** Run `bin/browser goto "$VERIFY_WEB/admin"`. `bin/browser text` contains `ERRORE 403 · ACCESSO NEGATO` (the eyebrow is CSS uppercase), `Questa pagina è riservata al team Zenbree`, the signed-in email and `Codice per il supporto: <8 chars>`. `bin/stack logs web | grep auth.forbidden` shows the same code.
- **API, non-admin.** With the session JWT, `curl -sS -w ' %{http_code}\n' -H "Authorization: Bearer $T" "$VERIFY_API/v1/admin/settings"` returns `{"detail":{"code":"AUTH.FORBIDDEN","message":"Admin only"}} 403`.
- **API key.** An `sbk_` key gets `401` on `/v1/admin/*`: the admin routes take only a session JWT.
- **Grant admin.** Run `bin/stack make-admin <email>`. It prints `<email>|admin`. This step is setup, not proof.
- **Open settings.**
  1. `bin/browser goto "$VERIFY_WEB/"`
  2. `bin/browser click button "Menu account"`, then `bin/browser click menuitem "Backoffice admin"`
  3. `bin/browser wait heading "Utenti"`
  4. `bin/browser goto "$VERIFY_WEB/admin/settings"` (with a switch on, the rail link's name also carries its counter)
  5. `bin/browser wait heading "Impostazioni"`. With no rows the page reads `Ancora niente qui.`
- **Add a key.**
  1. `bin/browser fill textbox "Chiave" "vendor_disabled:apollo"`
  2. `bin/browser click button "Aggiungi"`
  3. `bin/browser wait switch "vendor_disabled:apollo"`

  A `Salvato` toast appears. `bin/stack psql -tAc "SELECT key, value FROM settings"` prints `vendor_disabled:apollo|false`.
- **Add refused (#338).** With `vendor_disabled:apollo` switched on (Toggle below), fill `textbox "Chiave"` with `vendor_disabled:apollo` again and click `button "Aggiungi"`: `bin/browser wait text "Esiste già: usa l’interruttore nella lista"`, `ax` shows the textbox `[invalid]`, and the row still prints `true`. A key like `vendor_disable:apollo` shows `La chiave deve essere vendor_disabled: o channel_disabled: seguito dal nome del fornitore o canale` and adds no row. `curl -X POST` with the admin JWT and `{"key":…}` returns 409 / 422 with those codes.
- **Toggle.** Run `bin/browser click switch "vendor_disabled:apollo"`. An `alertdialog "Accendere vendor_disabled:apollo?"` opens and nothing is saved yet (the row still prints `false`). `bin/browser click button "Accendi"`: in `ax`, the switch shows `[checked]` next to `Bloccato` and «aggiornato … da <admin name>», and the same query prints `vendor_disabled:apollo|true`. The rail's Impostazioni counter goes up by one.
- **Confirm text (#639).** The `alertdialog` body names what the switch holds back, by key: a lookup vendor (`vendor_disabled:apollo`) says the agent stops calling it; `google`/`microsoft` name that provider's mailboxes; `channel_disabled:email` and `linkedin`, and `unipile` (which also stops chat LinkedIn search), say queued campaign steps wait and Inbox answers get a 503; `hubspot` says new steps wait, write-back waits and queued HubSpot task steps wait (queued sends still go); any other key says the system stops using it. No migration seeds the rows: add each key first (**Add a key**; a new key starts off), then click its switch, read the text and press Escape: nothing is saved. Read the `apollo` text before **Toggle**, because once it is on its switch asks `Spegnere`.
- **API view.**
  1. `T=$(bin/browser eval "fetch('/api/auth/token').then(r => r.json()).then(j => j.token)" | tr -d '"')`
  2. `curl -sS -H "Authorization: Bearer $T" "$VERIFY_API/v1/admin/settings"`

  The status is `200`, and the body is `{"settings":[{"key":"vendor_disabled:apollo","value":true,"updated_at":…,"updated_by":…,"updated_by_name":…}],"vendor_errors":[]}`.
- **Proof.** Save the `ax` dump and a screenshot of the checked switch, the `psql` row, and the curl body.

## Gotchas

- The add-key input's accessible name is `Chiave`; its placeholder is `vendor_disabled:apollo`.
- The role change applies on the next page load, because the JWT is minted per request. You do not need to sign in again.
- `Salvato` toasts stack up. Assert the switch state and the database row, not the toast count.
- `is_disabled` (outreach sends and the `channel_disabled:*` switches) fails open with a 30 s cache. The chat tools read the `settings` row on every call, uncached (`vendor_disabled_now` in `api/app/admin/settings.py`). Proving that the vendor is blocked needs a chat turn, which needs an LLM key and that vendor's key.
- `wait-url /admin` passes before the page renders. Wait for `heading "Utenti"` instead.
- Users, ban and impersonation in the same back office are in [admin users](admin-users.md).
- Vendors with failed calls in the last 24 hours (#352) are driven in [admin ops](admin-ops.md) with its seed.
- Refused adds driven 2026-10-07 on fix/338-admin-setting-key.
- Driven 2026-10-04 at 9c0ac03; the confirm and the `Bloccato` tag driven 2026-10-05 on feat/223-admin-screens.
- Driven 2026-10-10 at dae0d57: gate, API 403, add, toggle, `Accendi`, duplicate key.
- Confirm texts per switch driven 2026-10-12 on fix/admin-switch-copy.
