# Billing

Credits, the plan, the Stripe customer and the automatic top-up belong to the organization (#306): one wallet that every member spends. An admin of the organization sees the credit balance, the subscription plans, the top-up packs, the automatic top-up and the transaction history on Settings › Crediti (`/billing`); a member sees only the balance. Paying leaves for Stripe and returns to this page with a result banner.

## Sub-features

- `billing-balance` shows `region "SALDO ATTUALE"` with the single `credits_remaining` total. With a plan it also draws a two-segment bar (plan credits, then purchased credits) and a legend in text: `Del piano <n> · si azzera il <data>` and `Acquistati <n> · non scadono`.
- `billing-prices` (#349) shows `Quanto costa cosa` next to the balance, for members too: agent plan free, row Gratis, email 1 credito, phone 8 crediti, AI cell 1 credito. The table is static in `app/(app)/billing/page.tsx` and mirrors the API's `ToolDef` costs and `CELL_COST`.
- `billing-profile-needed`: while the profile is missing or pending, `link "Per acquistare o abbonarti compila i dati di fatturazione."` sits once above the plans and jumps to the profile; `button "Abbonati"` and `button "Acquista"` are disabled.
- `billing-plans` shows one card per plan. The current plan has the `Piano attuale` pill and its renewal date; the others offer `button "Abbonati"` (leaves for Stripe Checkout) or, for a subscriber, `button "Passa a <Piano>"`, which opens a confirm dialog and changes the plan in place through `POST /v1/billing/plan` (#417). An upgrade charges the price difference at once; a downgrade starts at the renewal.
- `billing-packs` shows one card per pack with the price and the price per 1.000 crediti on one line, and `button "Acquista"`.
- `billing-autotopup` saves the automatic top-up pack and threshold with `button "Salva"`.
- `billing-transactions` lists movements with a type tag, signed credits and the balance after, 50 per page with `link "Meno recenti"` and `link "Più recenti"`.
- `billing-member` is what a non-admin member sees: the credits box without `link "Ricarica"` (on a phone the pill is not a link), no `Crediti` tab or menu item, and on `/billing` (typed in) the balance card and `I crediti sono condivisi da tutta l'organizzazione. Solo i suoi admin li acquistano e gestiscono il piano.`, nothing else. The API answers its billing calls (`profile` GET/PUT, `subscription`, `auto-topup`, `transactions`, `invoices`, `checkout`, `subscribe`, `portal`, `plan`) with 403 `AUTH.ORG_ADMIN_ONLY`, and `GET /v1/me` reads `org_role: "member"` with the organization's `credits_remaining`.
- `billing-two-orgs` a user in two organizations spends, buys and sets the automatic top-up in the active one: the balance, the movements and the auto top-up settings change with `CAMBIA TEAM`.
- `billing-webhooks` credits the organization named in the Stripe object's `metadata.organization_id` (an object from before #306 carries only `user_id`: that user's organization), and plan renewals the organization of `billing_customers`.
- `billing-notices` shows `?checkout=success|cancel|error`, `?autotopup=saved|cancel|error`, `?plan=upgraded|downgrade_scheduled|downgrade_canceled|error` and `?profile=required|pending` banners.
- `billing-switch` (#323): `/billing/switch?org=<id>`, the link of a low-credit notice, makes that organization active and opens `/billing`; for an organization the user is not in it opens `/billing` and changes nothing ([team](team.md)).

## How to get to it (user POV)

- Choose `link "Impostazioni"`, then the last tab `link "Crediti"`.
- `link "Ricarica"` in the sidebar credits box and `menuitem "Crediti"` in `button "Menu account"` also open `/billing`.

## Driving it with bin/browser

Preconditions:

- Seed and sign in a user past onboarding, as in the README.
- Save a complete customer profile first: `region "Dati di fatturazione"` comes after `Movimenti`, and `button "Acquista"` stays `[disabled]` until a profile is saved and while its Stripe sync is `pending`. A valid Italian individual: choose `combobox "Tipo cliente"` › `Privato` first (the default `Azienda o professionista` asks for a Partita IVA and leaves `Codice fiscale` optional), then fill `Nome completo o ragione sociale`, `Email di fatturazione`, `Indirizzo e numero civico`, `Comune o città`, `Provincia o stato` `MI`, `CAP o codice postale` `20100` and `Codice fiscale` `RSSMRA85T10A562S`, then `button "Salva dati di fatturazione"`; the `status` reads `Dati di fatturazione salvati.` The billing email must not be a `@verify.local` address: the API rejects that special-use domain with 422 `BILLING.PROFILE_INVALID`, and the page shows `Controlla i dati di fatturazione…`. Use `billing@example.com`.
- The packs and plans lists are empty without Stripe price ids. Start the api with `BILLING_PACKS` and `BILLING_PLANS` set to JSON lists whose items carry a `stripe_price_id` (any string). Put them in a compose override and run `bin/stack compose -f <override>.yml up -d api`, with `DEEPSEEK_API_KEY` still exported if the run needs the LLM (`SKILL.md`). Items are `{"id","credits","price_eur_cents","stripe_price_id"}`.
- Seed movements (`<uid>` is the user's id; every row names the organization's wallet):
  `bin/stack psql -tAc "INSERT INTO credit_transactions (organization_id,user_id,type,amount,balance_after,description) SELECT default_organization(u),u,t,a,b,d FROM (VALUES ('<uid>'::uuid,'purchase',1000,1230,'Ricarica 1.000 crediti'),('<uid>','deduction',580,650,'fiere_2026.csv'),('<uid>','refund',14,942,'Righe non consegnate')) v(u,t,a,b,d)"`
- For the current plan: `INSERT INTO subscriptions (stripe_subscription_id,organization_id,user_id,plan_id,status,current_period_end,cancel_at_period_end,event_created) VALUES ('sub_x',default_organization('<uid>'),'<uid>','starter','active','2026-11-01',false,1)`. A plan cancelled in the portal (#538): also set `cancel_at` (e.g. `'2026-11-01'`); the page then reads «Termina il …».
- A member, and a user in two organizations: invite them from Settings › Team and accept ([team](team.md)): `Membro` for the member view, `Amministratore` for the two-organization admin. A new sign-in starts in the user's own organization: switch with `button "Menu account"` › `menuitem "<organization>"`.
- Webhooks: add `STRIPE_WEBHOOK_SECRET: whsec_<anything>` to the same api override and sign the events yourself: header `stripe-signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>" with the secret>`, then `POST $VERIFY_API/v1/billing/stripe-webhook`. Shapes as in `api/tests/test_billing_admin_sql.py` (`_paid_event`, `_invoice_event`). No Stripe call is made.

- **Open.** `bin/browser click link "Impostazioni"`, `bin/browser click link "Crediti"`, `bin/browser wait heading "Crediti"`. `ax` shows, in this order, `region "SALDO ATTUALE"`, `region "Abbonamento"`, `region "Ricarica"`, `region "Movimenti"` with a `table`, `region "Dati di fatturazione"`, `region "Ricarica automatica"` and `region "Documenti Stripe"`. The `heading "Crediti"` is visible to screen readers only.
- **Plans.** With the subscription row, the Starter card shows `Piano attuale` and `Si rinnova il 1 nov 2026`, and the Pro card shows `button "Passa a Pro"`. A cancelled (`Termina il …`) or unpaid (`Pagamento non riuscito…`) plan offers no switch, only the portal. `button "Gestisci abbonamento e fatture"` sits in the section header.
- **Packs.** Each card reads `<n> crediti` with thousands grouping (`2.000 crediti`), the price with `+ IVA` and `<price> ogni 1.000 crediti`. A whole-euro price drops its decimals (`19 € + IVA`), the per-1.000 price keeps two (1.000 crediti at 19 EUR reads `19,00 € ogni 1.000 crediti`). Plan cards read `29 €/mese + IVA` and `2.000 crediti al mese` the same way.
- **Checkout failure.** Without a Stripe key, `bin/browser click button "Acquista" --nth 0` returns to `/billing?checkout=error` and shows `status` `Impossibile avviare il pagamento. Riprova tra poco.`. The same happens for `button "Abbonati"` and the portal button.
- **Plan change.** `button "Passa a Pro"` opens `alertdialog "Passare a Pro?"`: `Paghi oggi 50 € + IVA, la differenza tra i due piani…` and `button "Paga 50 € + IVA"` (the name has a U+00A0 before `€`: pass `"Paga 50"$'\u00a0'"€ + IVA"`). A downgrade opens `alertdialog "Passare a Starter al rinnovo?"` with `button "Passa a Starter il 1 nov 2026"`. Without a Stripe key, confirming returns to `/billing?plan=error` with `status` `Impossibile cambiare il piano. Riprova tra poco.` and no credit moves. A scheduled downgrade (`subscriptions.scheduled_plan_id`, written only after Stripe accepts the schedule: seed it with SQL without a key) shows the `Dal 1 nov 2026` pill on the target card, `button "Annulla il cambio"`, and `· Passa a Starter il 1 nov 2026` on the current one.
- **Plan credits through webhooks.** Send `invoice.paid` events with `billing_reason` and plan lines (`{"amount", "pricing": {"type": "price_details", "price_details": {"price": <id>}}}`). An upgrade invoice (`subscription_update`, Starter line `-2900`, Pro line `7900`) tops plan credits up to 1.500 minus those used this period, once per invoice id. A prorated one (`-97`/`263`, the portal's) grants nothing and logs `stripe.plan.upgrade_underpaid`. A `subscription_cycle` invoice expires the leftover plan credits (`plan_expiry`) and grants the new plan's allowance; purchased credits stay. A `customer.subscription.updated` without `schedule` clears `scheduled_plan_id`.
- **Auto top-up.** `bin/browser select combobox "Quando sotto" "500 crediti"`, `bin/browser click button "Salva"`. The banner reads `Impostazioni della ricarica automatica salvate.` and psql `SELECT enabled, pack_id, threshold FROM auto_topups` shows `f|starter|500`. Reload: the select keeps `500 crediti`. Turning it on needs a Stripe card: without a Stripe key, checking `Attiva` and saving returns to `/billing?autotopup=error` with `Impossibile salvare la ricarica automatica. Riprova tra poco.`
- **Movements.** Each row has a type tag (`Utilizzo`, `Rimborso`, `Rinnovo`, `Acquisto`, `Bonus`, `Scadenza crediti piano`, `Rettifica`, `Prenotazione`), credits with a sign and the balance after. Credits and balance are right-aligned. A spend made in a chat shows `link "Apri chat"` (to `/?s=<id>`), an AI column fill `link "Apri lista"` (to `/lists/<id>`); other rows have no link.
- **Phone.** At `bin/browser viewport 390 844` the menu's `link "Crediti"` opens `/billing` under `link "Tutte le impostazioni"`. The balance, profile form and pack cards fit within the viewport width, and each movement is a card: type tag and signed credits, the description, then the date and `Saldo: <n>`. With the header row hidden, every card cell names its column for screen readers: `ax` reads `cell "Data: …"`, `cell "Tipo: Acquisto"`, `cell "Crediti: +1.000"`; at 1440 the cells are bare under `columnheader`s. `bin/browser eval` of any element wider than `innerWidth` with `--width 390` finds none.
- **Pagination.** With more than 50 rows, `link "Meno recenti"` goes to `?offset=50` and shows `link "Più recenti"`.
- **Member.** Signed in as the member and switched to the team, the sidebar credits box shows the team's balance with no `link "Ricarica"`, and `navigation "Impostazioni"` has no `Crediti`. `goto /billing` shows `region "SALDO ATTUALE"` with the organization's balance and the admin-only sentence; there is no `region "Ricarica"`, `Abbonamento`, `Ricarica automatica` or `Movimenti`. curl the routes above with the member's token for the 403s. A purchase webhook for the organization then moves the member's balance too (reload).
- **Two organizations.** As the user who is admin of their own organization and of the team: in the team, save the automatic top-up at one threshold; switch to their own and save another. `SELECT organization_id, user_id, threshold FROM auto_topups` has one row per organization. A signed purchase webhook naming the team (`metadata.organization_id`) moves only the team's balance: the sidebar reads it in the team and the old balance after switching back. psql `credit_transactions` rows name the team.
- **Proof.** Save `bin/browser ax --out "$VERIFY_ARTIFACTS/billing/page.ax.txt"`, a screenshot, and the psql rows.

### Stripe document history (`billing-invoices`)

The `Documenti Stripe` region lists payment documents and explicitly distinguishes them from
Italian SDI delivery. Use `VERIFY_ID=billing-history-final`, `VERIFY_WEB_PORT=13020` and
`VERIFY_API_PORT=18020` on every helper invocation when other worktrees are being verified.

- Sign in a freshly seeded user and open `/billing`. The region shows `Nessun documento
  disponibile.`; no portal action appears before a Stripe customer exists. Capture page text,
  screenshot and a JWT-authenticated `GET /v1/billing/invoices` response (redact token).
- For the missing-configuration state only, seed a `billing_customers` row with the active `organization_id` and purchaser `user_id` in
  the disposable verify database. Reload: `Impossibile caricare i documenti. Riprova tra poco.`;
  API returns 503 `BILLING.INVOICES_UNAVAILABLE`. This setup does not prove a real purchase.
- Change the disposable user's membership to `member`, then remove it. With the previously
  minted JWT, the documents endpoint denies members with 403 `AUTH.ORG_ADMIN_ONLY` and
  removed members with 401 `AUTH.NOT_A_MEMBER`. The member UI hides the entire history region.
  A global admin role does not override organization membership.
- Actual paid and draft history, hosted/PDF links, organization-admin portal for pack-only purchases and
  multi-page provider pagination require a Stripe test account with real test invoices.
  Without that prerequisite, report skipped; focused SQL/provider-boundary tests do not
  count as real app proof for those journeys.
- With real test invoices, change invoice page via `Meno recenti` inside this region and
  ledger page inside `Movimenti`. Confirm `invoiceCursor` survives ledger navigation and
  `offset` survives invoice navigation. `Più recenti` in documents resets to latest invoices.
- Repeat the document history at phone width and in English; verify no horizontal overflow.


#### Positive test account proof

Use a private compose override to supply only an authorized Stripe **test** key to the isolated
API; keep the credential file outside the repository. Reapply the override after `bin/stack up`,
which restores the normal verify API environment. Wait for API health before navigating again.
Link the synthetic test customer's id to the active organization in the disposable verify DB.
Use actual test invoices covering draft/open/paid/void/uncollectible and more than 20 records.

- In the document region, verify €10.00 net + €2.20 tax = €12.20 total and draft links absent.
  Drive both invoice and ledger pagination; seed more than 50 ledger rows for this purpose only.
  Wait for settled URL and row counts before capturing evidence after an RSC navigation.
- Click a hosted-document link and capture the loaded Stripe TEST page. Fetch the displayed
  PDF URL to an artifact, confirm HTTP 200, PDF content type and `%PDF-` bytes.
- Sign in a second admin of the billed organization. Its own organization remains empty; use
  the Account menu team switch, then reopen `/billing` (switching goes to `/`). Confirm the
  same invoice ids and click the management button. Read back zero subscriptions and capture
  the loaded Stripe TEST portal. Do not confuse this with a successful pack/card purchase.
- Test fixtures marked paid out of band and an explicit test tax rate prove document display,
  pagination, downloads and portal access. They do not prove Checkout, Stripe Tax or SDI.
- Driven 2026-10-06 with real Stripe test documents and two org admins after #306/#308 integration.

### Customer fiscal profile (`billing-profile`)

Use a dedicated disposable stack (`VERIFY_ID=billing-profile`, web13031/API18031) and synthetic
customer details. The profile is customer information, never the issuer's actual fiscal PII.

- Admin opens `/billing`: the profile form comes after the packs and `Movimenti`. With no saved profile,
  purchasing a pack/subscription or enabling top-up directs attention to the missing profile.
  Disabling top-up still succeeds; documents and the existing customer portal remain available.
- Save an Italian individual with a syntactically valid CF, name/email, address, recognized
  two-letter province and five-digit CAP. Reload and confirm every field retained. No Stripe
  customer means saved locally; do not claim provider synchronization from that notice.
- Save an Italian business with native `eu_vat` and a synthetic test VAT number. Reload.
  Country/type switches show appropriate native business ID options; unsupported business
  destinations show an explicit limitation. Foreign individual fiscal identifiers are required,
  but postal code can be empty in countries without postcodes (e.g. AE/HK).
- Bad CF/VAT country, fake country, unknown IT province, malformed email/PEC and overlong fields
  must fail without echoed PII. Capture the validation notice and sanitized API response.
- Existing Stripe TEST customer: save profile; retrieve native customer name/email/address,
  metadata and Tax IDs outside the repository with secrets redacted. Business→individual
  clears business IDs and sets `fiscal_code`; individual→business clears fiscal_code and creates
  native business ID. Re-saving identical business ID retains one ID. Verify unrelated metadata
  remains and PEC is local only; no fabricated `tax_exempt` flag or PEC routing metadata.
- Pack/subscription/card setup use the authoritative profile. Inspect test Checkout response
  and actual hosted screen; prove Stripe Tax amounts from provider records rather than assuming
  22% from UI text. A paid-out-of-band fixture is not a completed card checkout.
- For an existing customer with an unavailable Stripe key, profile PUT returns an error while
  retaining local pending fields. Enabled/awaiting-card top-up becomes off. Retry successfully
  and confirm it stays off until explicitly re-enabled. Capture DB/API state without credentials.
- Repeat IT/EN and phone390: all inputs/buttons fit; save/reload, errors and disabled actions
  remain understandable. Second admin reads the same organization profile. Member is denied403,
  removed membership401, and another active organization cannot read/change this profile.
- Preserve screenshots/AX readback, sanitized authenticated API results, provider TEST readback
  and DB state. Record external checkout/card/SDI paths unavailable honestly. The connector's
  documented mapping and Stripe test proof do not establish A-Cube SDI delivery.

#### Profile proof retained 2026-10-06

The dedicated billing-profile stack proved real TEST card4242 consumer and German-business pack
payments (€12.20 including €2.20 tax / €10 with zero tax), native Tax ID replacement and B2C
cleanup, actual invalid-native-ID failure with pending profile/top-up disabled, and recovery via
the app form. Monthly Checkout opened (€35.38), but no paid subscription/renewal was claimed.
English populated phone390 showed no horizontal overflow. Second-admin/shared profile and
separate own-organization null profile were read back through authenticated real APIs.
No A-Cube SDI or live-account payment was exercised. Handoff records exact evidence paths/limits.

Real no-card setup also returned 200 and saved awaiting_card=true/enabled=false. After a native
fiscal sync failure disabled both flags, completing the older actual TEST card 4242 setup Checkout
and forwarding its webhook left top-up disabled. This checks the stale completion path with a
real provider session in addition to the SQL regression coverage.

## Gotchas

- Prices put a non-breaking space (U+00A0) between the amount and `€`, so `grep -F "19 € + IVA"` with a plain space fails on `bin/browser text`. Match `19.€ + IVA` with a regex, or read `ax`.
- The portal button with no `billing_customers` row also lands on `?checkout=error` (a 404 `BILLING.NO_CUSTOMER`, not a Stripe failure).
- Seeded `credit_transactions` rows do not change the balance: the sidebar and the balance card read `user_credits`.
- A deduction with `reference_type = 'plan_expiry'` shows as `Scadenza crediti piano`, not `Utilizzo`.
- The balance card of a subscriber shows a stacked meter over `Del piano <n> · si azzera il <data>` and `Acquistati <n> · non scadono` (`balance.fromPlan` and `balance.fromPurchased`; read in source, not driven 2026-10-16) (`GET /v1/me`: `plan_credits_left`, `plan_credits_reset_at`, `purchased_credits`). Seed a period with a `credit_transactions` row `reference_type='stripe_plan_period'` (type `renewal`) and set `user_credits.credits_remaining` to match, since seeded rows do not move the balance.
- Stripe external journeys require explicitly supplied test credentials. Without them report skipped; never use production credentials for verification.
- Plan change (#417) driven 2026-10-09 without a Stripe key: dialogs in Italian, English and at 390 wide, the error notice, the scheduled display, the not-changeable states and the credits through signed webhooks. The Stripe calls themselves are #541.
- Organization wallet, member view and signed webhooks driven 2026-10-06 for #306, on a stack upgraded from `main` (1fe0fca) with an existing customer, subscription and open hold; members and two organizations again through invites on top of #308.
- Phone layout driven 2026-10-05 for #302. Driven 2026-10-04 at 9c0ac03, with `BILLING_PLANS`/`BILLING_PACKS` set through a compose override.
- Driven 2026-10-07 at 40a7ebc: `billing-prices` in light, dark, English and at 390 wide (it stacks under the balance).
- Driven 2026-10-10 at dae0d57 with `BILLING_PLANS`/`BILLING_PACKS` set: the profile save, the checkout error, the `Passa a Pro` dialog and its error, the auto top-up, movements, the member view and its 403s, and `/billing/switch`.
- Driven 2026-10-16 at c2551a38 with `BILLING_PLANS`/`BILLING_PACKS` set: the region order, the pack and plan cards, an Italian individual profile (`Privato`), the checkout failure and the auto top-up save.
