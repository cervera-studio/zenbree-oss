# Zenbree verification map

This directory is the maintained source for verifying Zenbree's user-facing behavior. Read this index before driving the app, then use the matching feature file as the recipe. When a proof covers only some of a feature's entry points, it is incomplete. Report the entry points you skipped.

## Baseline preconditions

- Start the stack with `bin/stack up`. `bin/stack doctor` must print `doctor: OK` with `build: current`.
- Load the URLs with `eval "$(bin/stack env)"`. Web is `$VERIFY_WEB` (http://localhost:13000) and API is `$VERIFY_API` (http://localhost:18000).
- Start the browser with `bin/browser start`. The UI is Italian (`NEXT_LOCALE=it`), and every name below is the Italian accessible name.
- Each verify stack starts with an empty database. Every user gets 25 free credits at sign-up (`DEFAULT_SIGNUP_CREDITS` in `web/lib/signup-credits.ts`). The `settings` row `signup_bonus_credits` overrides it: after the [admin ops](admin-ops.md) seed, which sets it to 100, every new user gets 100.
- Never drive the developer's stack on :3000/:8000.

## Driving conventions

- Commands are literal and run from `.claude/skills/verify/`. Keep the quoted names unchanged.
- Targets are ARIA role + accessible name from `bin/browser ax`. If a name differs from the recipe, the recipe is stale: run `ax` again, and fix the map.
- Create users with `bin/stack seed-user <email> verify-pass-1234`, except in [auth](auth.md), which proves sign-up itself. Use `@verify.local` addresses.
- A new user lands on `/onboarding`. To reach the app, choose `button "Salta per ora"`.
- To sign in from the browser:
  1. `bin/browser goto "$VERIFY_WEB/auth/sign-in"`
  2. `bin/browser fill textbox "Email" <email>`
  3. `bin/browser fill textbox "Password" verify-pass-1234`
  4. `bin/browser click button "Accedi"`
- To sign out in the app, click `button "Menu account"` (sidebar foot) and choose `menuitem "Esci"`. On `/onboarding`, the header has a plain `button "Esci"`.
- Navigate the way a user does. This proves the user path; use `goto` only for a page that has no link.
  - The sidebar is `navigation "Navigazione principale"`. From top to bottom it has:
    - `button "Cliente: Il mio cliente"` (the client switcher, [clients](clients.md)) and `button "Notifiche"` beside it.
    - `button "Cerca ⌘K"` (the phone drawer: `button "Cerca"`, no hint) opens `dialog "Cerca"` (#828), also on ⌘K / Ctrl+K from the rail (`bin/browser press` has no modifiers: `bin/browser eval 'window.dispatchEvent(new KeyboardEvent("keydown",{key:"k",metaKey:true,bubbles:true}))'`). `combobox "Cerca"` filters by every word; groups `LISTE`, `CHAT`, `CAMPAGNE`, `IMPOSTAZIONI` of links (lists and campaigns of the active client, loaded on open); ArrowDown/ArrowUp move, Enter follows the highlighted link and closes (the drawer too), Escape closes, `Nessun risultato` when nothing matches.
    - `link "Nuova chat"` (to `/`, the app home).
    - The main rows, with no group title: `link "Panoramica"` (to `/`), `"Chat"`, `"Liste"`, `"Viste salvate"`, `"Campagne"` and `"Risposte"`. `Liste`, `Viste salvate`, `Campagne` and `Risposte` show a live count (#342): the number of lists that have at least one row (the empty list of a search that found nothing does not count), of saved views and of campaigns, and on Risposte a pill with the unread replies (none at 0). The count is the link's description, not its name, so `ax` prints `link "Liste"` and the count separately. The phone drawer adds `link "Da approvare"` (to `/campaigns`) with the messages waiting for approval.
    - `region "CHAT RECENTI"`, the recent chats: one link per conversation, named by its title, or the text `Ancora nessuna conversazione.` with none. Under a title sit a status dot and a meta line (`In corso · 3 di 10`, `Aspetta una tua risposta`, `Piano da approvare`, `2 liste · 47 persone, 31 aziende`, `Errore in un passo`), the link's description. Counts and chats update live from `GET /v1/home/nav/stream`, with no reload. Only the newest 8 (pinned first) are listed, and no `link "Tutte le chat"` follows them: `link "Chat"` opens `/chats`, the full list: every chat of the active client, `searchbox "Cerca tra le chat"` that filters by title as you type, and per chat `button "Fissa «<title>»"` (pressed when pinned) and `button "Elimina «<title>»"` (asks first, in `alertdialog "Eliminare «<title>»?"` with `button "Elimina"`; disabled while the chat answers, and the API refuses with 409 `CHAT.JOB_RUNNING`)).
    - `region "WORKSPACE"`, with `link "Esclusioni"`, `"Team e ruoli"`, `"Integrazioni"` and `"Impostazioni"`, in that order.
    - The credits card, plain text with no `progressbar` (the ticks are `aria-hidden`): `Crediti`, the balance, and with a live plan ` / ` and the plan's credits per period (`plan_credits` of `GET /v1/me`; the balance can pass it). Then `Saldo <balance>. Il piano dà <plan> crediti a ogni periodo.` for a screen reader, and `<n> crediti del piano: si azzerano il 14 nov`, where `<n>` is the plan credits left (`plan_credits_left`), not the balance: purchased credits never expire and are not in it. With no plan, only `Crediti` and the balance. For an admin of the organization the card ends with `link "Ricarica"` (to `/billing`).
    - The profile `link "<name> <role line>"` (to `/settings/profile`). The role line is `Admin` for an admin of the active organization, else the company or the email. In the phone drawer the credits card is gone: the link reads `<name> <role line> · <n> crediti`.
    - `button "Menu account"`.
  - Chrome applies CSS `uppercase` to accessible names, so the section regions read `CHAT RECENTI` and `WORKSPACE` in `ax`, not the casing in the messages file.
  - Settings pages are tabs in `navigation "Impostazioni"`: `Profilo`, `Notifiche`, `Mittenti`, `Integrazioni`, `Memoria`, `Esclusioni`, `Cliente`, `Team`, `API key` and `Crediti` (`/billing`); a member of the organization (not its admin) sees neither `API key` nor `Crediti` ([team](team.md)). Above the tabs, `heading "Impostazioni"` (the one `h1`); each page names itself with an `h2`. Below 760 px the tabs are gone: `/settings` is a list menu with the same links (`Profilo` opens `/settings/profile`), and every page has `link "Tutte le impostazioni"` back to it ([settings profile](settings-profile.md)).
  - `button "Menu account"` opens `menu "Menu account"`, which holds:
    - `menuitem "Profilo e impostazioni"` (to `/settings/profile`).
    - `menuitem "Crediti"` (to `/billing`), for admins of the organization only.
    - `menuitem "Aiuto e guide"` (to the landing's FAQ, `<NEXT_PUBLIC_LANDING_URL>/#faq` in Italian or `/<locale>/#faq` in another language, which ends on the support address; hidden with no landing set).
    - Under `CAMBIA TEAM`, one `menuitem "<organization name>"` each, for a user in two or more organizations ([team](team.md)).
    - `menuitem "Nuovo team"`, for everyone: `dialog "Nuovo team"` with `textbox "Nome del team"` and `button "Crea team"` ([team](team.md) `team-create`).
    - `menuitem "Backoffice admin"`, for admins only (its name includes the "admin" badge).
    - `menuitem "Esci"`.
- The browser window is 1440 wide (desktop layout). Below 760 px the sidebar becomes a drawer behind `button "Apri il menu"`, and the top bar has the bell and a `link "<n> crediti"` pill: see [system pages](system-pages.md).

## Proof and skip reporting

- **Location.** Write artifacts to `$VERIFY_ARTIFACTS/<feature-id>/`.
- **UI proof.** Save an `ax` dump and a screenshot of the resulting state.
- **Mutation proof.** Add a read-only second view: a `bin/stack psql -tAc` row, a `curl` response with its status code, or the page after a reload.
- **Redaction.** Replace keys, JWTs and verification tokens with `<redacted>`.
- **Missing vendor key.** If a step needs a vendor key that the stack lacks (LLM, Unipile, Stripe, mail OAuth, Resend email), report it as skipped with the reason. Do not report it as verified.

## Feature entry contract

Each feature file starts with an H1 and one paragraph that describes the user-visible behavior. It then has exactly four H2 sections, in this order:

1. `Sub-features` lists short ids, with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with bin/browser` starts with `Preconditions:` and pairs each user action with an exact command and an observable result.
4. `Gotchas` lists traps that can waste or invalidate a run.

## Features

- [Auth](auth.md) covers sign-up, verification, sign-in and its errors, sign-out, the gate, the expired and ban notices, password reset, and the one-person organization every user gets (#305) with the upgrade to it. Driven, except OAuth.
- [Clients](clients.md) covers projects as clients (#307): the client switcher, a new client's profile step, each client's own profile, exclusions, senders, campaigns and inbox, who may disconnect a sender, the monthly credit cap (hard/soft) and its settings page (`Cliente`, #327), and the backfill on a stack upgraded from main. Driven, except a chat turn (no LLM key).
- [Team](team.md) covers invites (invite, accept, decline, expire, the invitee's list), role changes, removal and the immediate API refusal, the member's view and 403s, the organization switcher and the pinned owner role and slug (#308); the invitation throttle, sign-up from an invitation, shared runs and saved searches, a removed member's chat stream and the team's low-credit notice (#323). Driven, except email delivery.
- [API keys](api-keys.md) covers creating, using, listing and revoking an `sbk_` key, with curl proof against the API. Driven.
- [Senders](senders.md) covers the mailbox cards: status pill, pause and resume, reconnect, the sends progress bar and the DNS chips, the send window, and the organization's accounts with `Usa in un altro cliente` (#327). Driven with seeded `user_oauth_connections` rows.
- [Memory](memory.md) covers the memory document, its counter and save status, and the lock. Driven.
- [Integrations](integrations.md) covers the mailbox rows, the LinkedIn risk gate and connected card, the HubSpot card and the OAuth result notices. Driven, except the Unipile and HubSpot connects.
- [Instantly](instantly.md) covers connecting an Instantly workspace on Integrazioni, and sending the checked people of a list to a campaign: pick, map, preview, the running-campaign consent, the per-row results with the campaign link, a timeout left uncertain, cancel and retry, and the campaign events of #930 (webhook, reconciliation beat, sync status on the card). Driven against `bin/fake-instantly`; no real workspace and no real email.
- [Billing](billing.md) covers the organization's credits balance, plans, packs, auto top-up and transactions on `/billing` (admins; members see only the balance) and the Stripe webhooks. Driven with `BILLING_PLANS`/`BILLING_PACKS` set on the api.
- [Saved searches](saved-searches.md) covers saved searches (no UI yet): create, pause, run, and a free run that takes no hold, also at a wallet of 0 (#776). Driven (API only).
- [Watchlists](watchlists.md) covers watchlists and job-posting signals (#647): the API (no UI yet), a run by the beat against `bin/fake-apollo`, the signals and their score, the rotation, a vendor failure, pause and delete. Driven (API only).
- [Webhooks](webhooks.md) covers outbound signed webhooks, the organization's (#323): the API and Settings › Webhooks (#943), a member's event reaching the team's endpoint, the SSRF refusals, and delivery, retry and 410 deregistration driven by the dispatcher beat. Driven (API only).
- [Automations](automations.md) covers automations (#939, #940). By `curl` with stub vendors, it drives a manual and a scheduled run, the first run in review (`draft`), its approval, the credit cap and a run that fails. In the app, it drives the `/automations` pages with their runs, and the editor that creates, previews, publishes and deletes an automation, and the webhook trigger (#1031): the secret shown once, signed calls by `curl`, the queue and the refusals.
- [Exclusions](exclusions.md) covers the do-not-contact list: adding by type, duplicates, invalid values, CSV import, remove with undo and the API. Driven.
- [Saved views](saved-views.md) covers saving a list's tab, search and fit floor as a named view, the `Viste salvate` page (open, rename, delete with undo), the sidebar count and the API (#827). Driven.
- [Audience identity](audience.md) covers the identity that links rows of several lists by an exact email, LinkedIn URL or domain (#937): the row's aliases, a conflict and its resolution, an opt-out that follows an alias, and the 404s of another project. API only; driven through a CSV upload.
- [Segments](segments.md) covers saved rules over a client's people judged per identity (#938): preview, save and edit with a version, publish and refresh that write only changes, updates after a data change through the `evaluate_segments` beat and its pause switch, members, why, a bad rule's path and the 404s of another organization. API only; driven with `segments-seed.sql`.
- [Lists](lists.md) covers the lists index and detail pages, the row aside, ratings, exclusion, the export dialog and the phone layout, on the sample data that `bin/stack up` seeds for `dev@zenbree.local` (and the empty state for any other user). Driven on the real API.
- [Admin settings](admin-settings.md) covers the admin gate and vendor kill switches. Driven.
- [Admin organizations](admin-organizations.md) covers the back-office organizations list and search, an organization's members, clients with their cap, ledger, subscription and purchases, the credit adjustment on its wallet and the user's links to their organizations (#327). Driven.
- [Admin users](admin-users.md) covers the back-office user search, impersonation, ban and unban, and the role and session dialogs. Driven.
- [Admin transcript, costs and jobs](admin-ops.md) covers the chat transcript and its cost summary, the costs dashboard and its error state, the jobs list and the rail counters, on seeded data. Driven.
- [Chat](chat.md) covers the composer, a chat turn as a job, the session URL, the chat header (title, status pill, credits used), the list chips and the tabbed list panel. Driven: a real turn with an LLM key, the keyless failure path, the header on seeded chats with inserted messages and a live turn fed through Redis.
- [Onboarding](onboarding.md) covers the 3-step wizard and its skip path. Driven: redirect, skip, the keyless analyse failure, and with an LLM key the analyse, profile and segment steps.
- [Notifications](notifications.md) covers the bell panel, mark-all-read and the autosaving channel settings. Driven.
- [Settings profile](settings-profile.md) covers name, company, the email change request, the time zone, password, other devices, deleting your own account, language, theme and the settings tabs. Driven.
- [Public pages](public-pages.md) covers the redirect of the app's signed-out `/` to the landing, privacy, terms and the unsubscribe page (ask, done and undo, already done, invalid, one-click POST). Driven, the valid link through a compose override.
- [Landing](landing.md) covers the standalone Astro landing page: the four steps, FAQ accordion, links to the app, the closing composer and contrast in light and dark. Driven.
- [System pages](system-pages.md) covers the 404 and 403 pages and the phone layout. Driven.
- [Campaigns](campaigns.md) covers the campaigns index (tabs, progress, approval banner) and its empty checklist, creating a draft, the step builder, the preview, activation without a mailbox, deleting a draft, the active campaign page (C3) and the approval queue on desktop and phone (M4), and with #346 the preview sign-off and footer, the source list and `Riscrivi con l'agente`, the `Risposte` link with its badge, LinkedIn edits and the send line in the queue, and the auto-reply pause, and with #747 the bounces read from the mailbox (simulated DSN: stop, exclusion, refund, pause, notice, reconnect note). Driven; the test email, the LLM paths and a real provider DSN were not.
- [Campaign report](reports.md) covers the campaign report of #942: the range form and presets, the table of native and Instantly campaigns with people counted once, the unknown counts, the lineage disclosure and the Instantly note. Not driven yet.
- [Inbox](inbox.md) covers the replies list, the label and channel filters, relabeling and the phone view, on inserted replies, plus (#346) the campaign filter and chip, the auto-reply pause notice and the «Non ora» reminder, and (#421) the unverified-sender pill and notice. Driven.

A successful chat or onboarding turn needs an LLM key: export `DEEPSEEK_API_KEY` before `bin/stack up` (see `SKILL.md`). The stack has no search vendor keys, so a turn that searches ends with its plan steps in `Errore`, at 0 credits.

Each file's Gotchas name what was not driven. The valid unsubscribe link needs a compose override (see [public pages](public-pages.md)).
