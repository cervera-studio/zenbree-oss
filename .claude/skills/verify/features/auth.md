# Auth

A visitor creates an account with name, email and password, then opens the verification link from the email. The link verifies the account, signs the user in and lands them on onboarding. Later the user signs in and out with email and password, resets a forgotten password, and sees a notice when the session expired or the account is banned. Google and Microsoft sign-in need OAuth credentials that the verify stack does not have.

## Sub-features

- `auth-sentinel` identifies the browser through the configured project URL before auth requests; sign-up/sign-in still work when identification is unavailable. Infrastructure enforcement and proof-of-work challenges need a valid server `BETTER_AUTH_API_KEY`.
- `auth-sign-up` creates an unverified user and shows the "check your email" page.
- `auth-unverified` refuses sign-in until the email is verified.
- `auth-verify` opens the verification link, which verifies the user and signs them in.
- `auth-sign-in` signs in a verified user and opens the app.
- `auth-sign-in-error` rejects a wrong password with an inline alert.
- `auth-sign-out` ends the session from the account menu (or the onboarding header) and returns to sign-in.
- `auth-gate` redirects a request without a session to `/auth/sign-in?callbackUrl=<path>`. A signed-out `/` shows the landing page instead (see [public pages](public-pages.md)).
- `auth-expired` shows the expired-session notice on `/auth/sign-in?notice=expired`.
- `auth-reset` requests a reset link and sets a new password with it.
- `auth-banned` shows a banned user the suspension notice with reason and support address. The ban itself is set in the back office ([admin users](admin-users.md)).
- `auth-banned-while-open` (#762): a user banned while the app is open lands on `/auth/sign-in?notice=expired` with the suspension notice, not the expired one. A session revoked for any other reason, or a ban that is over, still shows the expired notice.

## How to get to it (user POV)

- Open `/auth/sign-up`, or switch between the two forms with `link "Accedi"` (under the sign-up form) and `link "Registrati"` (under the sign-in form). The landing page's `link "Inizia gratis"` also opens sign-up.
- Open any app URL while signed out, which redirects to sign-in.
- Sign out in the app: click `button "Menu account"`, then choose `menuitem "Esci"`. On `/onboarding` there is a plain `button "Esci"` in the header.
- Choose `link "Password dimenticata?"` on sign-in, which opens `/auth/reset-password`. `link "Torna ad accedere"` goes back.
- After sign-up, `/auth/verify` offers `button "inviala di nuovo"` and `link "Usa un altro indirizzo"`; the emailed link leads to the set-password page, so there is no continue button.
- Every auth page has a language switch in its footer: `button "English"`, `button "Italiano"`, `button "Español"` and `button "Français"` in `group "Lingua"`.
- Out of scope locally (no OAuth credentials): `button "Continua con Google"` and `button "Continua con Microsoft"` on sign-in, and `button "Registrati con Google"` and `button "Registrati con Microsoft"` on sign-up.

## Driving it with bin/browser

Preconditions:

- The baseline from the README holds and `bin/stack doctor` is OK.
- No user `auth@verify.local` exists: `bin/stack psql -tAc "SELECT count(*) FROM \"user\" WHERE email='auth@verify.local'"` prints `0`.

- **Sentinel.** Before submitting sign-up or sign-in, capture requests with the browser Performance API (`performance.getEntriesByType('resource').filter(e => e.name.includes('kv.better-auth.com')).map(e => e.name)`). Confirm the identify request uses the configured project URL. Repeat sign-in with identification blocked at the network boundary; it must complete or show the usual auth error. Record enforcement/challenge checks as skipped when no infrastructure API key is configured.
- **Gate.** Run `bin/browser goto "$VERIFY_WEB/lists"`, then `bin/browser url`. It prints `$VERIFY_WEB/auth/sign-in?callbackUrl=%2Flists`.
- **Sign-up form.**
  1. `bin/browser goto "$VERIFY_WEB/auth/sign-up"`
  2. `bin/browser fill textbox "Nome" "Verify Auth"`
  3. `bin/browser fill textbox "Email di lavoro" "auth@verify.local"` (sign-up says "Email di lavoro"; sign-in and reset say "Email")
  4. `bin/browser fill textbox "Password" "verify-pass-1234"`
  5. `bin/browser ax --out "$VERIFY_ARTIFACTS/auth/sign-up-filled.ax.txt"`
  6. `bin/browser click button "Registrati"`
  7. `bin/browser wait-url /auth/verify`, then `bin/browser wait text "Controlla la tua email"`

  `bin/stack psql -tAc "SELECT \"emailVerified\", (SELECT credits_remaining FROM user_credits c WHERE c.user_id=u.id) FROM \"user\" u WHERE email='auth@verify.local'"` prints `f|25` (`f|100` after the [admin ops](admin-ops.md) seed, which sets `signup_bonus_credits`).
- **Unverified sign-in.** On `/auth/sign-in`, fill `textbox "Email"` and `textbox "Password"`, click `button "Accedi"`, then `bin/browser wait css "form [role=alert]:not(:empty)"`. The alert reads `Conferma prima la tua email: ti abbiamo mandato un link.`
- **Verify.** Run `bin/browser goto "$(bin/stack verify-link auth@verify.local)"`, then `bin/browser wait-url /auth/reset-password`: the first verification wipes the password chosen at sign-up and lands on `heading "Imposta la tua password"` (`&new=1`). `bin/browser fill textbox "Nuova password" "verify-pass-1234"`, `bin/browser click button "Salva password"`, then `bin/browser wait-url /onboarding` (signed in with the sign-up address from `localStorage`). The page has `button "Salta per ora"` and the `emailVerified` query prints `t`. From an invitation, `verify-link <email> /invite/<id>` carries `callbackUrl` through and lands on the invitation after the password (#979, driven 2026-10-17).
- **Sign out (onboarding header).** `bin/browser click button "Esci"`, then `bin/browser wait-url /auth/sign-in`.
- **Wrong password.** Fill the email and `wrong-pass-0000`, click `button "Accedi"`, wait for `css "form [role=alert]:not(:empty)"`. `bin/browser eval "document.querySelector('form [role=alert]').innerText"` prints `"Email o password non corrette."`.
- **Sign in.** Fill `verify-pass-1234`, click `button "Accedi"`, `bin/browser wait-url /onboarding`, click `button "Salta per ora"`, `bin/browser wait text "Chi vuoi raggiungere per primo?"` (the first-access Home, see [chat](chat.md)). `bin/browser eval "fetch('/api/auth/token').then(r => r.json()).then(j => j.token.split('.').length)"` prints `3`.
- **Sign out (app).** `bin/browser click button "Menu account"`, then `bin/browser click menuitem "Esci"`, then `bin/browser wait-url /auth/sign-in`.
- **Expired notice.** `bin/browser goto "$VERIFY_WEB/auth/sign-in?notice=expired"`. The `status` reads `Sessione scaduta` followed by `Per sicurezza ti disconnettiamo dopo un periodo di inattività o quando reimposti la password. Accedi di nuovo per riprendere da dove eri rimasto.`
- **Reset.**
  1. `bin/browser click link "Password dimenticata?"`, then `bin/browser wait-url /auth/reset-password`
  2. `bin/browser fill textbox "Email" auth@verify.local`, then `bin/browser click button "Invia link"`. The `status` reads `Se l'indirizzo esiste, riceverai un'email con il link.`
  3. `bin/browser goto "$(bin/stack reset-link auth@verify.local)"`. It lands on `/auth/reset-password?token=…` with `textbox "Nuova password"`.
  4. `bin/browser fill textbox "Nuova password" "verify-pass-5678"`, then `bin/browser click button "Salva password"`. The `status` reads `Password aggiornata. Ora puoi accedere.`
  5. Sign in with the new password and wait for `text "Chi vuoi raggiungere per primo?"`. `bin/stack psql -tAc "SELECT count(*) FROM verification WHERE identifier LIKE 'reset-password:%'"` prints `0`: the token was used up.
- **Reset token stays out of analytics (#430).** Needs a PostHog key baked into the web image: `bin/fake-posthog > ph.log &`, then `export NEXT_PUBLIC_POSTHOG_KEY=phc_verify_dummy NEXT_PUBLIC_POSTHOG_HOST=http://localhost:18766` before `bin/stack up`. PostHog drops a bot's events, so start the browser as a desktop one: `VERIFY_USER_AGENT='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36' bin/browser start` (it also hides `navigator.webdriver`). `bin/browser goto "$VERIFY_WEB/auth/reset-password?token=MARKER"`, then `click link "Torna ad accedere"` and wait ~8 s: `ph.log` has `POST /e/` batches with `$pageview` for `/auth/reset-password` (no query) and `/auth/sign-in`, `/flags` carries `$initial_current_url` with `token=<masked>`, and `grep -c MARKER ph.log` prints `0`.
- **Banned.** After [admin users](admin-users.md) bans the user, sign in with the right password and wait for `css "[role=alert]"`. It reads `Account sospeso`, the expiry line (`La sospensione non ha una data di fine.` when none), `Motivo: <reason>` and `Pensi sia un errore? Scrivi a info@cervera.studio`.
- **Banned while the app is open (#762).** `bin/browser` has one tab, so the admin acts with curl on the same Better Auth endpoints the back office calls: sign in with a cookie jar (`curl -c "$VERIFY_STATE/admin.jar" -H 'content-type: application/json' -H "origin: $VERIFY_WEB" -d '{"email":"<admin>","password":"<pw>"}' "$VERIFY_WEB/api/auth/sign-in/email"`), then POST `{"userId":"<id>","banReason":"Spam verificato","banExpiresIn":604800}` to `/api/auth/admin/ban-user` with `-b` and the same headers.
  1. Sign the target in from the browser and stay on `/`. A session from before the fix has no marker cookie: sign in again on the build under test.
  2. Ban from curl. Poll `bin/browser url` once a second: the open streams re-check the ban, and within about 20 s their `AUTH.NOT_A_MEMBER` frame refreshes the page to `/auth/sign-in?notice=expired`.
  3. `css "[role=alert]"` reads `Account sospeso`, `Potrai accedere di nuovo dal <date>`, `Motivo: Spam verificato` and the support line; no `status` with `Sessione scaduta`.
  4. Not a ban: unban (`/api/auth/admin/unban-user`), sign in again, then POST `/api/auth/admin/revoke-user-sessions` (or reset the password with `reset-link` and `/api/auth/reset-password`) and reload: the `status` reads `Sessione scaduta` and there is no `alert`. A revoke sends no stream frame, so the page stays until the reload.
  5. A ban that is over: ban with `"banExpiresIn":1`; the reload shows `Sessione scaduta`.
  6. The window after the session dies: with a jar from the target's sign-in, ban, then `curl -i -b <jar> -c <jar> "$VERIFY_WEB/api/auth/token"`. The 401 expires `better-auth.session_token` and sets `better-auth.signed_in_user` with `Max-Age=600`; a second call sets no marker. The app's own token retry does the same.
  7. The notice comes from the server only: with the target banned, `curl "$VERIFY_WEB/auth/sign-in?notice=expired"` without cookies, with `-b "better-auth.signed_in_user=<id>"` or with a forged signature renders no `role="alert"`. With a jar from the target's own sign-in it renders the notice. `GET /api/auth/signed-in-ban` is 404: the endpoint is server-only.
  8. A ban while no tab is open: sign the target in, `bin/browser goto about:blank`, ban, then `bin/browser goto "$VERIFY_WEB/lists"`. The app layout cannot set cookies, so it redirects to `/auth/expired?callbackUrl=%2Flists`. That route expires `better-auth.session_token`, sets the marker with `Max-Age=600` and redirects to `/auth/sign-in?notice=expired&callbackUrl=%2Flists`, which shows the ban notice. `bin/browser` cannot read httpOnly cookies: read them over CDP (`Network.getAllCookies` on the port in `$VERIFY_STATE/browser.json`), or curl `/lists` and then `/auth/expired` with the browser's cookies and read the `set-cookie` lines.
- **Organization (#305).** After sign-up, `bin/stack psql -tAc "SELECT m.role, o.slug = u.id::text FROM member m JOIN organization o ON o.id = m.\"organizationId\" JOIN \"user\" u ON u.id = m.\"userId\" WHERE u.email = 'auth@verify.local'"` prints `admin|t`, and the JWT (`fetch('/api/auth/token')`, middle part base64-decoded) carries that `org`. Creating organizations stays shut: `fetch('/api/auth/organization/create', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{"name":"x","slug":"x"}'})` is 403, and so is `set-active` with another user's organization; `delete` is 404. Invites, roles and removal are in [team](team.md).
- **Upgrade from before organizations.** Build the stack from a `main` without #305 (`git worktree add --detach`, `VERIFY_ID=<id> bin/stack up` there), sign in as `dev@zenbree.local`, then `bin/stack up` with the same `VERIFY_ID` from this checkout: `bin/stack logs migrate` lists `*_organizations.sql`, the open session keeps working, and every project, list, chat, campaign and `sbk_` key of the dev user is still there; a `seed-user` sees none of them (404 by id).
- **Proof.** Save the `ax` dumps, a screenshot of the home and of the ban notice, the `emailVerified` values before and after the link, and the reset `verification` count.

## Gotchas

- The verify stack sends no email (no `RESEND_API_KEY`; the web logs `auth.email.failed` with `RESEND_API_KEY or EMAIL_FROM not set`). `verify-link` and `reset-link` rebuild the links Better Auth would have mailed and go through the real endpoints. They prove the links work, not that an email was delivered. Report delivery as skipped.
- `reset-link` needs a pending request: submit the reset form first. Each submit creates a new token; `reset-link` returns the newest.
- In Italian, the sign-in title and the submit button are both "Accedi". Target `button "Accedi"`, not `text "Accedi"`.
- The wrong-password and unverified alerts come from Better Auth's error code, not its English message, so they follow the UI language (`errors.AUTH_CLIENT.*` in `web/messages/errors.*.json`).
- `wait-url "$VERIFY_WEB/"` matches every URL. To detect the app home, wait for `text "Chi vuoi raggiungere per primo?"` after `Salta per ora` (the first-access Home), or `heading "Panoramica"` once that screen is closed (`Nascondi`, or a profile and a list; with a list the Home is the data view). Signed out, `/` is the landing page.
- The account menu trigger is the icon-only `button "Menu account"`. The user's name is in the separate profile `link "<name> <company or email>"` next to it, not in the button: the second line is the onboarding company name when there is one, else the email.
- Per the source (`verify-email.tsx`), the verify page's resend button appears only while sessionStorage holds the sign-up email, so a fresh browser opening `/auth/verify` should not show it. Only the same-browser case has been driven.
- `/auth/sign-in?error=BANNED_USER` shows the ban notice without a reason, which is a quick check of the notice; the real ban in [admin users](admin-users.md) is the proof for the reason line.
- Seeded users (`bin/stack seed-user`) skip the browser sign-up form. Never use them as proof of `auth-sign-up`.
- Phone (#302): headless Chrome has no on-screen keyboard. Stand in for it with a short viewport: on each form, `bin/browser eval "<focus the last input, then read the submit button's getBoundingClientRect().bottom>" --width 390 --height 460`; the bottom must be within 460. The fields' scroll margin is what keeps it there; a real iOS keyboard was not driven.
- Many sign-ins from one host in about a minute hit Better Auth's rate limit: the form shows `Troppe richieste. Attendi un momento e riprova.` Wait a minute, then sign in again.
- The ban marker (`better-auth.signed_in_user`, httpOnly) is set on each new session that is not an impersonation, cleared on sign-out and on a successful delete, and cut to 10 minutes by the first response that drops the dead session cookie: the app's token retry, or `/auth/expired`. Until that response the marker keeps its full lifetime, so the first app visit after the ban shows the ban notice to whoever opens it, and so does any visit in the 10 minutes after it. `app/admin/layout.tsx` and `app/onboarding/page.tsx` redirect to plain `/auth/sign-in` and cut nothing. The social callback sets it through the same Better Auth code, but it was not driven (no OAuth credentials).
- Driven 2026-10-04 at 9c0ac03, all sub-features except OAuth. Driven again 2026-10-10 at dae0d57, except OAuth.
- Driven 2026-10-14 on fix/762-ban-notice: `auth-banned-while-open` steps 1 to 7, and the marker on email sign-in, verify-email auto sign-in, impersonate, stop-impersonating and sign-out (curl jars). Driven again the same day after the `/auth/expired` route: steps 2 to 4 and 8.
- Driven 2026-10-16 at c2551a38: the gate, sign-up, the unverified alert, verify, sign-outs, a wrong password, the expired notice, reset, and the banned notice after [admin users](admin-users.md). The language switch has four buttons.
