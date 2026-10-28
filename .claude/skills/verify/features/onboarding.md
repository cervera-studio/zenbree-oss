# Onboarding

A new user goes through a 3-step wizard at `/onboarding` before reaching the app:

1. Company: a website or a description, which the agent analyses.
2. A sales profile to review.
3. Proposed segments. Picking one builds the first list in a chat.

The user can skip the wizard at any step.

## Sub-features

- `onb-redirect` sends a new user, who has no completed profile and no chats, to `/onboarding`.
- `onb-skip` completes onboarding without a profile and opens `/`.
- `onb-prefill` (#350) fills `Sito web` from the domain of the user's email when the project has no stored website or description. A free-mail domain (`gmail.com`, `outlook.it`, …, `web/components/onboarding/email-site.ts`) leaves it empty. The sign-up form says so under `Email di lavoro`: `Dal dominio capiamo per quale azienda lavori.`
- `onb-analyse` reads the website or description and fills the profile. It needs an LLM key. Since #350 the API streams the progress (SSE), and `region "Analisi in corso"` ticks off its rows: `Leggo il sito` with `N pagine`, `Capisco cosa vendi e a chi` with the profile's summary, then `Preparo il profilo di vendita`.
- `onb-cancel` (#350): `button "Cambia sito"` (`button "Modifica descrizione"` without a website) in the analysis panel closes the request, returns to the form with the inputs kept and the focus on the field, and shows no error. The API stops the study; a cancel before the LLM call gives the free run back.
- `onb-profile` lets the user edit and confirm the profile, then proposes segments. It needs an LLM key.
- `onb-exclusions` turns the profile's `Da non contattare mai` lines that name a company, domain, email or LinkedIn URL into Exclusions rules tagged `source = 'onboarding'` (#193); a later save removes only those. It needs no LLM key.
- `onb-segment` picks a segment, which starts the first chat job at `/?s=<id>`. It needs an LLM key.
- `onb-redo` (#349): `/onboarding?redo=1`, from `link "Rifai il profilo dal sito"` on Settings › Memoria, opens the wizard for a finished user at the company step with the saved website filled in. `Salta per ora` goes back to `/settings/memory` and leaves `completed_at` alone. Plain `/onboarding` still sends a finished user to `/`.

## How to get to it (user POV)

- Sign in for the first time, or open the verification link (`bin/stack verify-link` locally). Either lands on `/onboarding`.
- Choose `button "Salta per ora"` in the wizard header on any step. On the Segments step, `button "Scrivilo in chat"` also skips.
- Choose `button "Esci"` in the wizard header to sign out.

## Driving it with bin/browser

Preconditions:

- A user that has never finished onboarding: `bin/stack seed-user onb@verify.local verify-pass-1234`.
- The LLM key decides which steps can run: `onb-analyse`, `onb-profile` and `onb-segment` need one, as `bin/stack doctor` shows.

- **Redirect.** Sign in from the browser, as described in the README, then run `bin/browser wait-url /onboarding` and `bin/browser wait text "Parlaci della tua azienda"`. `ax` shows the stepper `list "Passo 1 di 3"` in the header, with `button "Salta per ora"` and `button "Esci"`. The form has `textbox "Sito web"` (after a fixed `https://` prefix), `textbox "Descrizione (facoltativa se hai un sito)"` and `button "Analizza"`. The site field holds the email's domain (`verify.local` for the seeded user, so `Analizza` is enabled); a free-mail user sees it empty and `Analizza` disabled.
- **Analyse without a key.** Start the stack with `VERIFY_LLM=off bin/stack up`.
  1. Fill `textbox "Descrizione (facoltativa se hai un sito)"` and click `button "Analizza"`. The form gives way to `region "Analisi in corso"`.
  2. Run `bin/browser wait css "[role=alert]:not(:empty)" --timeout 120000`.

  The alert reads `Non siamo riusciti ad analizzare la tua azienda. Riprova.` (`ONBOARDING.STUDY_FAILED`) and `user_credits.onboarding_free_used` stays `0` (the failed run is refunded).
- **Progress and cancel (no LLM key).** Start the stack with `VERIFY_LLM=off bin/stack up`. Seed the user with a real public domain (`bin/stack seed-user ada@python.org …`), so the site is readable.
  1. `bin/browser click button "Analizza"`, then `bin/browser ax` shows `Leggo il sito` with `image "In corso"` and, after the first pages, `text "2 pagine"`. Keyless, the run then ends in the same alert, and `bin/stack logs api` prints one `onboarding_study_failed`.
  2. `bin/browser click button "Analizza"`, then at once `bin/browser click button "Cambia sito"`. `bin/browser eval "document.activeElement.id"` prints `onboarding-website`, the field keeps its value, and no alert shows. `user_credits.onboarding_free_used` stays `0`, and the API logs no new `onboarding_study_failed`: the study stopped before the LLM call.
- **Skip.**
  1. `bin/browser click button "Salta per ora"`
  2. `bin/browser wait text "Chi vuoi raggiungere per primo?"` (the first-access Home, see [chat](chat.md) `chat-welcome`)
  3. `bin/stack psql -tAc "SELECT completed_at IS NOT NULL FROM business_profiles"`

  The query prints `t`. `GET "$VERIFY_API/v1/onboarding"` returns `"needs_onboarding":false` with `profile: null`. Signing out and in again lands on `/`, not `/onboarding`.
- **Analyse (needs an LLM key).**
  1. `bin/browser fill textbox "Sito web" "<host of a real public site, without https://>"`
  2. `bin/browser click button "Analizza"`
  3. `bin/browser wait text "Il tuo profilo" --timeout 150000`, then check that `ax` shows `list "Passo 2 di 3"`.

  The profile fields are filled:
  - `textbox "Nome dell'azienda"`.
  - The text areas `In breve`, `Come li risolvi` and `Prove e risultati`.
  - The list items, each a `textbox "<label>, voce <n>"` (`Chi compra`, `Problemi che risolvi`, `Segnali d'acquisto`, `Mercati`, `Da non contattare mai`) with a `button "Rimuovi <value>"`, and an `Aggiungi` button per list.

  `button "Indietro"` goes back to step 1.
- **Segments (needs an LLM key).**
  1. Choose `button "Conferma e proponi segmenti"`, then wait for `text "Scegli il primo segmento"`.
  2. Choose a `button "Crea la prima lista"` (`--nth` picks the card). The step also has `button "Modifica il profilo"`, `button "Proponi altri segmenti"` and `button "Scrivilo in chat"`.
  3. Run `bin/browser wait-url "?s="`.

  The chat sends `Crea la mia prima lista di contatti per il segmento "<segment>"…` and runs at once. The stack has no search vendor keys, so every search fails (#787): `region "Passi del piano"` shows the `alert` `La ricerca non è andata a buon fine e non hai usato crediti. Riprova tra poco.` with `button "Riprova"`, the pill reads `Ricerca non riuscita` and the job ends `succeeded` with 0 credits.
- **Profile step (no LLM key).**
  1. Seed a full profile (every Profile key, or the web's contract check fails and the gate lets the user in): with the user's session token, `PUT /v1/onboarding/profile` with `{"company_name":"Acme","summary":"CRM per agenzie","personas":["CEO"],"markets":["Italy"],"pain_points":[],"solution":"","buying_triggers":[],"exclusions":[],"proof":""}` (the profile is the active project's since #307; the API creates the default project on that first call). Reload `/onboarding`: the wizard opens on `Il tuo profilo`, whose `Dove lo uso` rail ends with `In ogni chat.`
  2. Edit `textbox "Nome dell'azienda"` and click `button "Conferma e proponi segmenti"`. The profile is saved before segments are proposed, so the step works even when segments then fail.
  3. `bin/stack psql -tAc "SELECT profile->>'company_name' FROM business_profiles WHERE project_id = <the active project>"` shows the new name; `user_memory_doc` is untouched (#307: the agent reads the client's profile, not Memory).
- **Profile exclusions to rules (no LLM key).** Seed a full profile as above with `"exclusions":["HubSpot","Clienti attuali"]`, plus a user rule (`POST /v1/exclusions {"items":[{"kind":"domain","value":"mine.com"}]}` with the same token). On `Il tuo profilo` the field hint reads `Finiscono in Esclusioni: nessuna lista li conterrà.` Add a line with the fifth `button "Aggiungi"` (`--nth 4`: `--nth` counts from 0), fill `textbox "Da non contattare mai, voce 3"` with `rival.com`, confirm. `bin/stack psql -tAc "SELECT kind, value, coalesce(source,'user') FROM exclusions ORDER BY value"` lists `hubspot` and `rival.com` as `onboarding` and `mine.com` as `user`; `Clienti attuali` is not a rule. Clear voce 1, click `button "Rimuovi voce vuota"` and confirm again: only `hubspot` goes. `/settings/exclusions` (after `Salta per ora`) lists the rules.
- **Proof.** Save `bin/browser ax` and a screenshot for each step reached, and the `business_profiles` row.

## Gotchas

- `button "Analizza"` stays disabled until a website or a description is filled. The seeded `@verify.local` users get `verify.local` prefilled, which no fetch can read. With a key and no description, `Analizza` on it ends in the `alert` `Non riusciamo a leggere il sito. Descrivi la tua azienda in poche righe e riprova.` (`ONBOARDING.SITE_UNREADABLE`), and the free run is refunded.
- A user who already has a chat session skips onboarding even without a profile.
- When `GET /v1/onboarding` fails or takes more than 2.5 s, the app layout lets the user in without onboarding. A slow API therefore looks like "onboarding skipped".
- Free LLM runs are capped at 10 per organization, counted on its wallet (`user_credits.onboarding_free_used`), not per user or per client. Every analyse and every segment proposal (`Conferma e proponi segmenti`, `Proponi altri segmenti`) counts; a failed run is refunded. After the cap, the alert shows the limit message.
- "Passo N di 3" is only the stepper's accessible name; the page shows no such text. `bin/browser text` and `wait text "Passo …"` do not find it. Wait on the step heading instead (`Parlaci della tua azienda`, `Il tuo profilo`, `Scegli il primo segmento`).
- Driven 2026-10-08 on the #350 branch: the sign-up hint (`aria-describedby="email-hint"`), the prefill (`python.org`), the progress rows (`2 pagine`) and a cancel during the site read (focus on the site field, free run kept, no LLM call). The offer summary row needs an LLM key.
- Driven 2026-10-05: profile exclusions to rules (add, remove, user rule kept, Exclusions page).
- Driven 2026-10-07 at 40a7ebc: `onb-redo` entry, website prefill (set by SQL), skip back to Memory with `completed_at` unchanged, and the keyless analyse failure. A successful redo needs an LLM key.
- Driven 2026-10-10 at dae0d57 with a DeepSeek key: analyse of `python.org` (a seeded `@python.org` user, so the site is prefilled), profile, segments and `Crea la prima lista`. An analyse takes up to about 2 minutes: run the wait in the background.
- Driven 2026-10-04 at 9c0ac03: redirect, the keyless analyse failure, skip and sign-out. The successful analyse, profile and segment steps need an LLM key; their names come from the source.
- Driven 2026-10-16 at c2551a38 with a DeepSeek key: the redirect, the unreadable `verify.local`, the analyse of `python.org`, segments and `Crea la prima lista`, which ended in `Ricerca non riuscita`.
