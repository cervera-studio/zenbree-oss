# System pages

Unknown URLs get a 404 page that names what was missing, offers a button for it and a card with the other places to go. A signed-in user without the right role gets a 403 page that names their email, offers `link "Torna all’app"` (typographic apostrophe: copy the name from `ax`) and `button "Accedi con un altro account"`, and shows a support code that also appears in the web log. Below 760 px the app switches to the phone layout: the sidebar becomes a drawer behind a menu button.

## Sub-features

- `sys-404-page` shows "Questa pagina non esiste" for an unknown path.
- `sys-404-resource` shows a resource-specific 404, such as "Questa campagna non esiste più", inside the sidebar shell.
- `sys-403` shows "Questa pagina è riservata al team Zenbree" with a support code.
- `sys-phone` shows the phone header and the navigation drawer below 760 px.

## How to get to it (user POV)

- Open a URL that does not exist, or a campaign id that does not exist.
- Open `/admin` as a non-admin.
- Use a window narrower than 760 px, then choose `button "Apri il menu"`.

## Driving it with bin/browser

Preconditions:

- A user from `bin/stack seed-user`, signed in and past onboarding, not an admin (for 403). The dev seed user `dev@zenbree.local` is an admin.

- **404 page.** `bin/browser goto "$VERIFY_WEB/nope"`. `ax` shows `heading "Questa pagina non esiste"`, `link "Torna alla home"` and a `navigation "Forse cercavi"` with the user's two newest lists when signed in with lists, else `link "Le tue liste"` and `link "Le tue campagne"`. This page is outside the shell: no sidebar.
- **404 resource.** `bin/browser goto "$VERIFY_WEB/campaigns/00000000-0000-0000-0000-000000000000"` shows `heading "Questa campagna non esiste più"` inside the sidebar, `link "Vai alle tue campagne"`, `link "Inizia una nuova chat"` and, in `navigation "Forse cercavi"`, the user's two newest lists (`<name> · N persone|aziende`), or `link "Le tue liste"` for a user without lists; the card has no «Nuova chat», the secondary button covers it. `/campaigns/nope` shows the same page, and `/lists/nope` the list version of it: each page checks that the id is a UUID before it calls the API (#278).
- **403.** `bin/browser goto "$VERIFY_WEB/admin"`. `bin/browser text` shows `ERRORE 403 · ACCESSO NEGATO`, the signed-in email and `Codice per il supporto: <code>`; `bin/stack logs web | grep auth.forbidden` has the same code. `button "Accedi con un altro account"` signs out and lands on `/auth/sign-in`.
- **Phone.**
  1. `bin/browser viewport 390 844` (the window clamps to 500 wide, still under 760)
  2. `bin/browser goto "$VERIFY_WEB/lists"`. The top bar (`banner`) has `button "Apri il menu"`, `link "Zenbree"`, `button "Notifiche"` and the credits pill: `link "<n> crediti"` (to `/billing`) for an admin of the organization, plain text for a member.
  3. `bin/browser click button "Apri il menu"` and `bin/browser wait dialog "Navigazione principale"`. The drawer has these controls:
     - `button "Chiudi il menu"`.
     - `link "Nuova chat"`, `"Panoramica"`, `"Chat"`, `"Liste"`, `"Campagne"`, `"Da approvare"` and `"Risposte"`.
     - The client switcher `button "Cliente: <name>"`, and one link per recent chat. There is no `link "Tutte le chat"`.
     - `region "WORKSPACE"` with `link "Esclusioni"`, `"Team e ruoli"`, `"Integrazioni"` and `"Impostazioni"`, and the profile `link "<name> <role line> · <n> crediti"`. There is no credits card and no `Ricarica`.
     - `button "Menu account"`.
  4. `bin/browser click link "Campagne"`, `bin/browser wait-url /campaigns`, `bin/browser wait-gone dialog "Navigazione principale"`: navigating closes the drawer.
  5. `bin/browser viewport 1440 900` to restore the desktop layout.
- **Proof.** Save screenshots of the 404, the 403 and the open drawer.

## Gotchas

- `/lists/<unknown id>` gets the resource 404 (`Questa lista non esiste più`) inside the sidebar: the list page calls `notFound()` on `LIST.NOT_FOUND`.
- The drawer's name is `dialog "Navigazione principale"`, the same as its inner `navigation`. Copy the name from `ax`.
- Headless Chrome cannot make the window narrower than 500 px. That still exercises every `phone:` rule but not the narrowest phones.
- The viewport persists for the browser session. Restore it before driving desktop recipes.
- Driven 2026-10-04 at 9c0ac03, after the 404 and 403 redesign (#231).
- Driven 2026-10-10 at dae0d57: 404, the campaign 404, 403 and the phone drawer.
