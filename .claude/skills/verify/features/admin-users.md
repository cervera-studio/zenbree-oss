# Admin users

An admin finds users in the back office (`/admin`, Utenti), opens a row's actions and bans, unbans, revokes sessions, changes the role or impersonates the user. The back office is a shell (A1–A7 of the 2 Oct handoff): a rail `navigation "Backoffice"` with the sections (Utenti with the user total, Organizzazioni, Costi, Job and Impostazioni with their counts), a `note` band «Produzione · le modifiche valgono per tutti» above the page, Flower under «Strumenti» when `NEXT_PUBLIC_FLOWER_URL` is set, `link "Torna all'app"` and the signed-in admin. While impersonating, the app shows an accent bar with the target's email, the admin who started it, the session expiry and the exit, and every API call runs as the target. Leaving impersonation returns to `/admin`.

## Sub-features

- `admin-users-search` filters the user table by email; the `Tutti` / `Admin N` / `Bannati N` chips filter by role or ban and keep the search.
- `admin-users-columns`: next to Better Auth's columns the table shows Piano (live subscription, `Solo ricariche` with purchases only, else —), Saldo, Ultima attività (latest Better Auth session, «Oggi, 19:43») and the company under the name (`business_profiles.profile.company_name`), from `GET /v1/admin/users/overview`.
- `admin-users-empty` is the no-results card (`Nessun utente trovato`, `link "Azzera ricerca e filtri"`; past the last page, `?offset=500`, it says «Nessun account in questa pagina»); `admin-users-loading` is the A1b skeleton (`loading.tsx`, shown on a client navigation to Utenti).
- `admin-user-detail` is A2: header with balance, the plan tag (`<Piano> · rinnovo <data>`, else `Nessun piano`), the `Organizzazioni:` links with the user's role in each, `button "Impersona"`, `link "Rettifica crediti"`, the row menu (`button "Azioni"`, whose first item `menuitem "Job"` opens `/admin/jobs?user=<id>`), and the section links `Utilizzo`, `Movimenti N`, `Rettifica crediti`, `Sessioni chat N` (`?tab=ledger|adjust|chats`).
- `admin-user-notfound` is A2b: `/admin/users/<unknown id>` answers 404 with `heading "Utente non trovato"` inside the shell.
- `admin-users-impersonate` starts acting as a user; the bar and `/v1/me` show the target.
- `admin-users-stop` leaves impersonation and drops the impersonated session.
- `admin-users-ban` bans a user with a reason; the user's sessions are revoked and sign-in shows the ban notice ([auth](auth.md) `auth-banned`).
- `admin-users-ban-effects` (#389): the ban stops the user's running jobs (the web calls `POST /v1/admin/users/{id}/after-ban`) and pauses their active campaigns, scheduled searches and webhooks; `Rimuovi ban` resumes those three, never the jobs.

## How to get to it (user POV)

- As an admin, click `button "Menu account"` and choose `menuitem "Backoffice admin"`. The Utenti page is the back-office home.
- Each row has `button "Azioni"`, which opens `menuitem "Rendi admin"`, `"Banna"` (or `"Rimuovi ban"`), `"Revoca sessioni"` and `"Impersona"`.
- While impersonating, the app shows a `status` bar above the sidebar with `button "Termina e torna al backoffice"`.

## Driving it with bin/browser

Preconditions:

- Two seeded users: the target (for example `auth@verify.local`) and an admin (`bin/stack seed-user adm@verify.local verify-pass-1234 Adm`, then `bin/stack make-admin adm@verify.local`).
- Signed in as the admin and past onboarding.

- **Search.** In the back office, `bin/browser fill searchbox "Cerca per email" "auth@"`, then `bin/browser press Enter` (there is no Cerca button). `ax` lists only `link "auth@verify.local"` and one `button "Azioni"`.
- **Filters.** `bin/browser click link "Bannati 1"` (the count is part of the name) gives `?filter=banned`; with a search that matches nobody, the no-results card shows the query in «…». `link "Azzera ricerca e filtri"` returns to `/admin`. The header line counts every account, those created in the last 7 days and the banned ones (seed an old account with `UPDATE "user" SET "createdAt" = now() - interval '30 days'` to see the weekly count move).
- **Detail.** Click the row's email. `Rettifica crediti` opens `?tab=adjust`: the form shows «Saldo attuale → Dopo la rettifica» and a «Quando usarla» panel. `click button "+50 · cortesia"` fills `spinbutton "Importo (negativo = addebito)"` with 50 and an empty `textbox "Motivo"` with «Credito di cortesia»; the submit reads `button "Aggiungi 50 crediti"` (`Togli N crediti` for a negative amount, `Applica` while the amount is empty). An amount below the balance (`click button "−100"` on a 50 balance) shows an `alert` «Il saldo non può andare sotto zero: puoi togliere al massimo 50 crediti.» (at a balance of 1, «al massimo 1 credito») and disables the submit, before any request. The amount reads as the number input does: `1e3` gives `button "Aggiungi 1.000 crediti"`, `1.5` leaves `Applica` disabled. A server error (a blank `Motivo` of spaces gets a 422) shows under the form until the next submit or `button "Annulla"`, which also clears amount and reason. After a submit, wait for `Salvato`: the balance in the header moves and `link "Movimenti N"` lists a `Rettifica (admin)` pill with a signed amount (`+50` green, `−30` plain). Other types show as Utilizzo, Riserva, Rimborso, Rinnovo piano, Ricarica, Bonus, Rettifica. Usage needs `vendor_calls` / `purchases` rows to show the per-vendor table and purchases (insert them with `bin/stack psql` as setup).
- **Not found.** `bin/browser goto "$VERIFY_WEB/admin/users/00000000-0000-0000-0000-00000000dead"`: the shell stays, the card has `link "Torna agli utenti"` and `link "Apri Costi"`.
- **Impersonate.**
  1. `bin/browser click button "Azioni"`, then `bin/browser click menuitem "Impersona"`
  2. The app opens on `/` (or `/onboarding` for a target that never finished it: `Salta per ora`). `ax` shows a `status` with `Stai usando Zenbree come <email>. Vedi quello che vede l'utente; ogni azione resta registrata a nome di <admin name>.`, `scade tra <N> min` and `button "Termina e torna al backoffice"`. The detail page's `button "Impersona"` does the same.
  3. With the session JWT, `curl "$VERIFY_API/v1/me"` returns the target's email and `"role":"user"`.
  4. `bin/stack psql -tAc "SELECT u.email FROM session s JOIN \"user\" u ON u.id=s.\"userId\" WHERE s.\"impersonatedBy\" IS NOT NULL"` prints the target.
- **Stop.** `bin/browser click button "Termina e torna al backoffice"`, then `bin/browser wait-url /admin`. The impersonated-session count is `0`.
- **Ban.**
  1. Search the target, `bin/browser click button "Azioni"`, `bin/browser click menuitem "Banna"`
  2. In `dialog "Banna <email>"` (its text says what a ban does), `bin/browser fill textbox "Motivo (l'utente lo vede quando accede con email e password)" "Spam verificato"`, then `bin/browser click button "Banna"` and `bin/browser wait-gone dialog "Banna <email>"`
  3. The row's state cell reads `Bannato`. `bin/stack psql -tAc "SELECT banned, \"banReason\" FROM \"user\" WHERE email='<email>'"` prints `t|Spam verificato`, and the target has `0` rows in `session`.
  4. Sign out, sign in as the target: see [auth](auth.md) `auth-banned`. A target that has the app open when you ban lands on the ban notice by itself: see [auth](auth.md) `auth-banned-while-open`.
- **Ban effects.** A job must be running when you ban. With no LLM key, run a stand-in OpenAI-compatible endpoint on the host that streams one delta every 2 s, and recreate only the worker with `DEEPSEEK_API_KEY=fake` and `DEEPSEEK_BASE_URL=http://host.docker.internal:<port>/v1` (an extra `-f` override on the `bin/stack` compose command, `up -d --no-deps worker`). Put both values in the override file, not in the shell: a `VERIFY_LLM=off` stack drops a key exported in the shell. As the target, send a brief: `jobs` is `running` with a `reservation|reserved` hold. Seed (setup) an `active` and a `paused` campaign with one target each and a `saved_searches` row, all due about two minutes later (`activated_at` / `next_run_at`; `tool_input` must validate as `PeopleArgs`, e.g. `{"titles":["CEO"]}`). Ban from the back office: the API log has `POST /v1/admin/users/<id>/after-ban 200`, the job turns `cancelled` with `credits_spent 0`, its hold `released` with a `refund`, the balance back. Past the due time the worker's beats log `{'plan': {}}` and `{'queued': 0}`: no `outreach_campaign_sends`, no `saved_search_runs`. `Rimuovi ban`: the next beat plans `awaiting_approval` for the active campaign only (the paused one stays `paused`), and queues the missed search once (it then fails `search_failed` without an Apollo key) with `next_run_at` a day on. Webhooks and a ban that expires are proven by `api/tests/test_ban_sql.py`.
- **Proof.** Save the users `ax` dump, a screenshot while impersonating, the `/v1/me` body (ids redacted), and the `user` and `session` rows.

## Gotchas

- The search field is a `searchbox`, not a `textbox`.
- Every row has a `button "Azioni"` with the same name. Search first so only one row is left, or use `--nth`.
- The ban dialog has no expiry field in this build; a ban without expiry shows `La sospensione non ha una data di fine.`
- `Rendi admin` / `Rimuovi admin` and `Revoca sessioni` ask first in an `alertdialog` (`Rendere admin <email>?`, `Revocare le sessioni di <email>?`) whose confirm button repeats the menu label. `Rimuovi ban` acts at once, with a `Salvato` toast and no dialog. `Banna` opens a plain `dialog` with the `Motivo` field.
- Driven 2026-10-04 at 9c0ac03: search, impersonate, stop, ban and the ban notice, `Rimuovi ban`, and the `Rendi admin` and `Revoca sessioni` dialogs up to `Annulla` (their confirm was not clicked).
- The back office has no `Menu account`: to sign out from `/admin`, go to `/` first.
- The expiry time is formatted in the time zone of whoever renders it: a full page load shows the server's (UTC in the stack), a client navigation the browser's.
- Light screenshots: `bin/browser eval "localStorage.setItem('theme','light')"`, then reload (the admin has no settings page of its own).
- Driven 2026-10-05 on feat/222-admin-shell: shell, search, filters, no results, skeleton, ban, detail usage/adjust/ledger, not found, impersonate from the row menu and from the detail page, stop. Light and dark, 1440 and phone.
- Driven 2026-10-07 on fix/390-391-backoffice-a11y: ban dialog hint, below-zero alert at balances 1 (singular) and 2, `1e3` preview, in it and en.
- Driven 2026-10-07 on issue-353: adjust form (shortcuts, below-zero alert, +50 and −30 applied and read back in `credit_transactions`), movement pills, ban dialog text. Light, dark and 390 wide.
- Driven 2026-10-07 on feat/389-ban-pauses-work: ban effects as above (job, hold, campaigns, scheduled search; ban and unban twice).
- Driven 2026-10-07 on fix/386-admin-polish: `1e3` applied as 1000 and read back in `credit_transactions`, `1.5` rejected, `Annulla` clearing a 422 error.
- Driven 2026-10-10 at dae0d57: search, detail header (plan line, `Organizzazioni:`, `Job`), adjust, below-zero alert, ledger, not found, impersonate and stop, ban and unban.
