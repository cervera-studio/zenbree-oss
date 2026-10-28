# Admin organizations

A platform admin (Better Auth `user.role` admin) browses every organization in the back office (#327): the list with members, plan and wallet balance, and an organization's detail with its members and roles, clients with their monthly cap and this month's spend, the wallet ledger (who did each movement), the subscription and purchases, and a credit adjustment on the organization's wallet with the same audit trail as a user's (`reference_type='admin'`, `reference_id=<admin id>`). A user's detail links to the organizations they belong to.

## Sub-features

- `admin-orgs-list`: `/admin/organizations`, newest first, columns Organizzazione, Membri, Piano, Saldo, Creato il; `searchbox "Cerca per nome o email di un membro"` matches the name, the slug or a member's email; `Nessuna organizzazione trovata` with `link "Azzera ricerca e filtri"` when nothing matches.
- `admin-orgs-detail`: header with `<n> membri · <n> clienti`, the balance and `link "Rettifica crediti"`; sections `Membri` (default), `Clienti`, `Movimenti N`, `Abbonamento e acquisti`, `Rettifica crediti` (`?tab=clients|ledger|billing|adjust`). An unknown id is the back office 404.
- `admin-orgs-adjust`: `spinbutton "Importo (negativo = addebito)"`, `textbox "Motivo"` and the submit button, which reads `Applica` (disabled) while the amount is empty, then `Aggiungi <n> crediti` or `Togli <n> crediti`; lands on the organization's oldest admin's account (never the back-office admin's). A negative amount larger than the balance shows the alert `Il saldo non può andare sotto zero: puoi togliere al massimo <n> crediti.` and sends nothing; the API answers 409 past the balance.
- `admin-orgs-user-link`: a user's detail reads `Organizzazioni: <org> (<Admin|Membro>)`, each a link to the organization.

## How to get to it (user POV)

- `button "Menu account"` › `menuitem "Backoffice admin"`, then `link "Organizzazioni"` in the rail.
- From a user's detail (`/admin/users/<id>`), the `Organizzazioni:` links; from an organization's members, each email links to the user's detail.

## Driving it with bin/browser

Preconditions: a platform admin (`bin/stack seed-user ops@verify.local verify-pass-1234 "Ops Verify"`, `bin/stack make-admin ops@verify.local`) and a team: seed Ada and Bo, and add Bo to Ada's organization in SQL (`INSERT INTO member ("organizationId","userId",role,"createdAt") VALUES (own_organization('<ada>'),'<bo>','member',now())`, renamed `Agenzia Ada`). A second client (`POST /v1/projects`) with a cap and a committed `deduction` carrying its `project_id` give the Clienti tab something to show.

- **Search.** `bin/browser fill searchbox "Cerca per nome o email di un membro" "bo@verify.local"`, `bin/browser press Enter`: two rows, Bo's own organization (1 member) and `Agenzia Ada` (2).
- **Detail.** `bin/browser click link "Agenzia Ada"`: `heading "Agenzia Ada"`, `2 membri · 2 clienti · dal <date>`, the Membri table (`Admin`, `Membro`). `link "Clienti"`: `500 crediti · blocca` and the month's spend per client.
- **Adjust.** `bin/browser click link "Rettifica crediti" --nth 1`, fill `100` and a reason, `button "Aggiungi 100 crediti"`: the header balance goes up by 100. An amount that would take the balance below zero (`-500` on 125) leaves `button "Togli 500 crediti" [disabled]`. `link "Movimenti 2"` shows the row with `Chi` = the oldest admin's email and `Rettifica (admin)`. Read back: `SELECT type, reference_type, reference_id, description FROM credit_transactions WHERE organization_id = '<org>'` (`reference_id` = the admin's user id) and `user_credits.credits_remaining`.
- **Deleted member (#323).** A member makes ledger rows (on a `VERIFY_LLM=off` stack, `POST /v1/runs` holds 50 and the failed job releases it), then the back-office admin deletes the account: `POST /api/auth/admin/remove-user {"userId": "<id>"}` with the admin's session. The balance is unchanged, `link "Movimenti 3"` still lists every row with `Chi` = `ex membro` for the deleted member (#333), and `SELECT user_id IS NULL FROM credit_transactions WHERE organization_id = '<org>'` is `t` for theirs. Search takes `%` and `_` literally: `?q=Ada_` finds nothing. Driven 2026-10-06 with `VERIFY_ID=323`.
- **User link.** From Membri open `link "bo@verify.local"`: `Organizzazioni: Bo Verify (Admin) Agenzia Ada (Membro)`; the second opens the organization.

## Gotchas

- `Rettifica crediti` appears twice (header button and section link): pass `--nth`.
- The `Saldo` of an organization with no wallet row yet reads `—`; the first adjustment creates it.
- A member's role pill follows `member.role`; Better Auth's `owner` is never used here (#308).
- Driven 2026-10-06 at 82a5a4a (`VERIFY_ID=327`, ports 13327/18327), desktop and phone, light and dark.
- Driven 2026-10-10 at dae0d57: search, detail, adjust, the below-zero alert, ledger and the user's `Organizzazioni:` link.
- Driven 2026-10-16 at c2551a38: search, detail, an adjustment of +100 and its movement row. The deleted member and the user link were not driven again.
