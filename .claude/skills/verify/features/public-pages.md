# Public pages

A signed-out visitor at the app's `/` is redirected to the marketing site (the Astro landing, [landing](landing.md)). The app serves the privacy policy at `/privacy` and the terms at `/terms`. Recipients of outreach email open `<UNSUBSCRIBE_ORIGIN>/x/<token>` (prod: `u.zenbree.com`; `/u/<token>` on the app host without it, and a 308 to the new host with it) to unsubscribe. None of these pages need a session.

## Sub-features

- `public-landing` redirects a signed-out `/`, and `/welcome` and `/welcome/...` for everyone, to `NEXT_PUBLIC_LANDING_URL` (307). The app has no marketing page of its own since 2026-10-15.
- `public-legal` shows the privacy policy and terms.
- `public-unsub` shows the unsubscribe page (C5 ask with optional reason, C5b done with undo and data-deletion request, C5c already, C5d invalid) on its own host, and the RFC 8058 one-click POST.
- `public-unsub-invalid` shows the invalid-link page for an unknown unsubscribe token.

## How to get to it (user POV)

- Open `$VERIFY_WEB/` signed out: the browser goes to the landing.
- Choose `menuitem "Aiuto e guide"` in the account menu: it opens the landing's FAQ.
- Choose `link "Prova gratis"` or `link "Accedi"` in the header of the privacy and terms pages.
- Follow `link "Privacy"` / `link "Termini"` / `link "Documentazione API"` in the public footer.
- Open the unsubscribe link from an outreach email.

## Driving it with bin/browser

Preconditions:

- Signed out (sign out, or a fresh browser profile).

- **Landing redirect.** The verify stack runs no landing, and the web image bakes in compose's default `http://localhost:4321`. Assert the `Location` header with curl, not the browser:
  - `curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "$VERIFY_WEB/"` prints `307 http://localhost:4321/en/` (English is the default language); with `-H 'Accept-Language: it'` or `-b NEXT_LOCALE=it` it prints `307 http://localhost:4321/`, and with `-b NEXT_LOCALE=es` it prints `307 http://localhost:4321/es/`.
  - `/welcome` and `/welcome/pricing` print the same, signed in or out. A link to `/welcome#faq` keeps `#faq` in the browser, because the fragment never reaches the server.
  - Signed in (a cookie jar from `curl -c jar -H "Origin: $VERIFY_WEB" -H 'content-type: application/json' -d '{"email":…,"password":…}' $VERIFY_WEB/api/auth/sign-in/email`), `curl -b jar "$VERIFY_WEB/"` answers 200 with the app.
  - `bin/browser eval` on the account menu's `menuitem "Aiuto e guide"` reads its `href`: `http://localhost:4321/#faq` in Italian, or `http://localhost:4321/<locale>/#faq` in another language.
- **Legal.** `bin/browser goto "$VERIFY_WEB/privacy"` shows `heading "Privacy policy"`. `bin/browser goto "$VERIFY_WEB/terms"` shows `heading "Termini di servizio"`.
- **Unsubscribe host setup.** (Only for the `/x/` host; the app host's `/u/<token>` needs just `OUTREACH_REPLY_SECRET`.) Use `127.0.0.1:$VERIFY_WEB_PORT` as the unsubscribe host (a different Host from `localhost`). Compose override: `OUTREACH_REPLY_SECRET` (any string) on api and worker, `UNSUBSCRIBE_ORIGIN: http://127.0.0.1:<web port>` on api, worker and web; `bin/stack compose -f <override>.yml up -d api worker web`. Seed a user (`seed-user`), a `business_profiles` row with `{"company_name": …}`, a `user_oauth_connections` google row (display_name, account_label), and a campaign + target + one `sent` email send with `sender_provider 'google'` and an `idempotency_key` (`gen_random_uuid()::text`) to the recipient. Mint the link's token as the dispatcher does: `bin/stack compose exec -T api python -c "from app.outreach.unsubscribe import mint; print(mint('<uid>','<email>',secret='<secret>'))"`.
- **Routing.** On the unsubscribe host only `/x/<token>` answers (curl `/`, `/welcome`, `/privacy`, `/auth/sign-in`, `/api/auth/jwks`, `/u/<token>` → 404). `localhost:<port>/u/<token>` → 308 to `http://127.0.0.1:<port>/x/<token>`; `$VERIFY_API/v1/unsubscribe/<token>` → 303 to the same.
- **C5.** `bin/browser goto "http://127.0.0.1:<port>/x/<token>"` (first set `document.cookie='NEXT_LOCALE=it; path=/'` and `localStorage.theme='light'` with `eval` on that host, then goto again: cookies are per host). `ax` shows `heading "Non vuoi più ricevere email da <company>?"`, four `radio`s and `button "Sì, non scrivetemi più"`. Phone: `viewport 390 844`.
- **C5b.** `click radio "Ricevo troppe email"`, `click button "Sì, non scrivetemi più"`, `wait text "Annulla la disiscrizione"`. Side effects: `psql` shows the email exclusion and the target `excluded`; `bin/stack logs api` shows `unsubscribe.reason` with `too_many`. `click button "Annulla la disiscrizione"` brings back the ask view and removes the exclusion. `button "Chiedi di cancellare i miei dati"` shows the failure text without `RESEND_API_KEY` (expected).
- **C5b paths.** The card is one reducer (`unsubscribe-state.ts`). With the send seeded, `click radio` then the confirm button gives done and `psql` shows one `exclusions` row. `button "Annulla la disiscrizione"` gives ask with the radio still checked and no row. The click always adds a durable exclusion, also with no seeded send (#333: `replies.unsubscribe`), and undo removes it. Undo answers 409 `UNSUBSCRIBE.UNDO_EXPIRED` only when the click added nothing (the address was already excluded) or the undo window passed: the undo link disappears and the card stays in done (read in source, not driven 2026-10-16). `button "Chiedi di cancellare i miei dati"` without `RESEND_API_KEY` shows `Non è stato possibile inviare la richiesta`; the sent text is not driven, because it sends an email. For the already-after-click path, load the ask view, `curl -X POST $VERIFY_API/v1/unsubscribe/<token>`, then click confirm: `Disiscrizione già fatta` with the undated body.
- **C5c.** Reload an unsubscribed link: `heading "Disiscrizione già fatta"` with «Dal <date>».
- **C5d.** `goto "http://127.0.0.1:<port>/x/not-a-token"`: `heading "Questo link non funziona più"`.
- **One-click.** `curl -X POST -H 'Content-Type: application/x-www-form-urlencoded' --data 'List-Unsubscribe=One-Click' http://127.0.0.1:<port>/x/<token>` → `{"status":"unsubscribed"}`; a bad token → 404.
- **No analytics on the unsubscribe page (#430).** With the stand-in PostHog host from [auth](auth.md) (reset token check), `goto` `/u/MARKER` and `/x/MARKER` (a fresh browser, `bin/browser stop` then `start`, so no earlier page initialised PostHog): the capture log gains no request at all, and `bin/browser eval "document.cookie"` has no `ph_` cookie. Driven 2026-10-08 on the app host without `UNSUBSCRIBE_ORIGIN` (the invalid-link page); the unsubscribe host serves the same `/x/` path.
- **Proof.** Save the redirect curl results, a screenshot of each unsubscribe state, the routing curl results and the `ax` headings.

## Gotchas

- Signed in, `/` is the chat home, not the landing. Sign out first.
- An empty, malformed or same-host `NEXT_PUBLIC_LANDING_URL` turns the redirect off: a signed-out `/` goes to `/auth/sign-in`, `/welcome` to the app's `/`, and the account menu has no `Aiuto e guide` (`web/lib/landing.ts`, `web/proxy.ts`).
- A valid unsubscribe token is a stateless token over the owner's user id and the recipient email, sealed with `OUTREACH_REPLY_SECRET` (`api/app/outreach/unsubscribe.py`). The verify stack sets none, so every token is invalid until `OUTREACH_REPLY_SECRET` is set on api and worker. That alone is enough for a valid link: with no `UNSUBSCRIBE_ORIGIN`, `unsubscribe.page_url` gives `WEB_ORIGIN/u/<token>`, which the app host serves (driven 2026-10-14 on #764). `UNSUBSCRIBE_ORIGIN` is only for the separate `/x/` host. With no `UNSUBSCRIBE_ORIGIN`, the one-click URL comes from `API_PUBLIC_BASE_URL`; when that is empty too, `unsubscribe.one_click_url` returns `None`, which is the verify stack's default. Start the stack with it exported, `API_PUBLIC_BASE_URL="$VERIFY_API" bin/stack up` (the stack's API origin, port `VERIFY_API_PORT`, 18000 by default; `compose.verify.yml` passes it to api and worker, and a rerun recreates both), and the URL is `{API_PUBLIC_BASE_URL}/v1/unsubscribe/<token>`; `bin/stack compose exec -T api python -c "from app.outreach.unsubscribe import one_click_url; print(one_click_url('T'))"` prints it (driven 2026-10-14).
- Unsubscribe driven 2026-10-05 for #220 with the override above.
- Driven 2026-10-05 for #224 (the web's `/welcome` landing, deleted 2026-10-15), with `BILLING_PLANS`/`BILLING_PACKS` set.
- Driven 2026-10-10 at dae0d57: the web's `/welcome` signed out with `Prezzi` and `Inizia con Pro`, `/welcome`, privacy, terms, and an invalid token on `/x/` and `/u/`. The valid unsubscribe link was not driven again.
- Landing redirect driven 2026-10-15: the curl checks above signed out and signed in, `/welcome#faq` in the browser, and the help link in Italian and English. The empty-setting cases were covered by unit tests only.
- Unsubscribe card driven 2026-10-12 for the reducer refactor: ask, done, undo, ask, refused undo, failed erasure, already on load and after a click.
- Driven 2026-10-16 at c2551a38: the redirects, legal pages and invalid links, then a valid `/u/<token>` with `OUTREACH_REPLY_SECRET` set and no `UNSUBSCRIBE_ORIGIN`: ask, done with the reason logged, failed erasure, undo, one-click and already. The `/x/` host was not driven.
