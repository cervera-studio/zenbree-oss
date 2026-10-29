# Customer fiscal billing profile

Branch `feat/billing-profile`, dedicated worktree `zenbree-billing-profile`, base `07d45a2`.

## Acceptance

Organization admins save/reload a complete validated fiscal customer profile; members cannot
read or change it. Pack/subscription checkout and enabling automatic top-up require the profile.
Disabling top-up and portal/document history remain available. Existing customer edits sync
before success so recurring invoices use updated native Stripe customer data and Tax IDs.
Provider failures retain a pending local profile and surface an error for retry.

Persist customer type, name/email/address (actual ISO country), fiscal identifier, business
Tax ID type/value, recipient code and optional PEC. Italian CF supports 16-character/11-digit
formats and VAT supports 11 digits; other destinations use appropriate native Stripe Tax IDs.
No invented reverse charge/tax exemptions or manual flat VAT. A-Cube receives documented
customer fields and fiscal_code/codice_destinatario metadata, not a fictional PEC mapping.

## Scope and dependencies

`api/app/billing/profile.py`, billing routes and router registration, one new migration and
focused/adapted billing tests; billing form/actions/page, billing client schemas/API and IT/EN
messages; contracts, this handoff and billing verify recipe. No new dependencies.
`api/app/billing/auto_topup.py` belongs to concurrent #323 hardening work and is not edited.
Shared contract changes are limited to the profile endpoints and checkout/profile requirement.
Root coordinates provider test setup, independent review and GitHub publishing.

Foreign individual fiscal identifiers are required by the chosen connector's documented B2C
mapping; no fake Italian CF or foreign fallback identifier is invented. Businesses in countries
without a supported native Stripe Tax ID cannot complete this connector profile; the UI/API
must disclose this limitation. Countries with supported IDs include destinations beyond Italy/EU.
PEC is local contact data only: the Stripe connector does not route invoices through PEC.

## Baseline

`./harness/init.sh all --fast`: PASS before implementation, supplied baseline log
`/tmp/zenbree-billing-profile-baseline.log`. Dedicated disposable DB is on localhost:55451.
Full checks completed below; isolated real app/provider proof and independent approval are recorded below.

## Implementation and focused checks

Profile GET/PUT uses shared live organization-admin authorization and a new organization-keyed
migration. PUT validates manually so errors do not echo fiscal data. Country/type options use
existing Stripe SDK native Tax ID types; the actual ISO list is compact static data (no new or
hidden transitive dependency). Provider updates are serialized with checkout/card setup and
configuration saves via a transaction advisory lock per organization. An existing customer
failure persists pending fields and disables enabled/awaiting-card top-up before returning 502.
Retry does not silently turn it on. Identical Tax IDs remain; changing IDs/customer type removes
stale IDs and clears fiscal_code for business. Native foreign identifier punctuation is retained.
Checkout uses authoritative saved fields and prevents collecting an extra business ID for B2C.

`uv run ruff check` / `uv run mypy`: PASS. Focused disposable-SQL/provider-boundary checks:
`TEST_DATABASE_URL=… uv run pytest tests/test_billing_profile.py tests/test_billing_admin.py
 tests/test_billing_admin_sql.py -q`: 72 PASS (log `/tmp/billing-profile-focused.log`).
Existing billing shape tests now expect profile gates/authoritative Tax ID data; setup regression
fixtures provide a valid profile. New tests prove live roles, two admins/shared profile, separate
organizations, PII-safe invalid input, missing-profile gates, disabling top-up without profile,
native tax-ID idempotence/clearing and pending+top-up disable for PUT/checkout/setup failures.

Known operational limit: an already-running charge and externally managed Stripe subscription
renewal cannot be atomically paused with local profile/provider updates. Pending failure prevents
future local top-ups; it does not cancel a subscription. The concurrently owned worker file is
untouched. This implementation maps documented A-Cube customer fields; no SDI issuance proof
or PEC delivery is claimed. UI/provider proof and independent final review are recorded below.

## Final full local gate

`TEST_DATABASE_URL=postgresql://postgres:harness@localhost:55451/zenbree_test ./harness/init.sh all`
completed PASS on the final source/tests: **701 API tests, 739 web tests, 59 landing checks**,
no skipped tests. Ruff format/lint, mypy (152 source files), Biome, TypeScript, knip,
Next production build, landing lint/check/build and harness unit checks passed. Final log:
`/tmp/zenbree-billing-profile-final.log`; initial full log `/tmp/zenbree-billing-profile-full.log`.
The final rerun includes five additional web schema/SSR checks for native identifier retention,
unknown state/type rejection, saved form rendering and unsupported-business save gating.
`git diff --check`: PASS. No lint suppressions, dependencies or existing migrations changed.

Before go-live the repository accepts full local checks, real app evidence and independent
review while GitHub Actions is intentionally unavailable. This task does not weaken that gate.

## Real app and Stripe TEST evidence

Root drove the isolated app through real Better Auth sessions and browser controls with
`VERIFY_ID=billing-profile`, web13031/API18031. Evidence is retained at
`~/.cache/zenbree-verify/billing-profile/artifacts/billing-profile`.

- Empty GET returns null; missing-profile checkout 409; invalid country 422 has no echoed input.
  Live organization members receive 403 for profile GET/PUT and checkout. SQL coverage also
  checks membership removal 401. Form saves/reloads complete customer data and renders localized
  validation/sync errors; unsupported business destinations are disclosed.
- Real **Stripe TEST card 4242** Checkout payments completed for an Italian consumer and a German
  business. Consumer pack: €10 net + €2.20 tax = €12.20; German business: €10 net + €0 tax.
  Provider records show `automatic_tax=complete`, and the app's wallet reached 2050 credits.
  This proves test Checkout/Stripe Tax calculation, not VIES registration validation.
- A monthly plan Checkout opened with €29 net + €6.38 tax = €35.38. It was not completed:
  no paid subscription/renewal proof is claimed.
- Native provider readback proves name/email/address, Italian business Tax ID and recipient code,
  then German business ID/address and foreign code, then business→consumer removal of native
  Tax IDs with fiscal_code restored. Unrelated organization/user metadata survived.
- A native invalid Canadian business identifier triggered actual provider 502 after local save,
  retaining pending profile and turning an enabled top-up off. Real UI correction/retry synced
  the profile; top-up remained off until explicitly re-enabled.
- English populated form at phone 390 had document width 390 and zero overflowing controls.
  Second organization admin GET returned 200 with the same synced owner-organization profile.
  Their separate own organization returned 200 with profile null. Switching used public Better
  Auth organization APIs from legitimately joined authenticated sessions.

The Stripe test account differs from the newly opened live account. No live customer/card/payment
was used, and no A-Cube SDI issuance/delivery, PEC delivery, paid monthly subscription renewal or
VIES validity proof is claimed. Final frozen-source stack rebuild and doctor: CURRENT/OK, database/Redis health and JWKS OK.
`final-build-checkout.json` returned 200 and `final-build-stripe.json` confirmed provider tax 220 /
total 1220 on a newly created session. Second-admin UI edit `SharedAdminEdit` persisted after this
rebuild. Independent reviewer verdict: **APPROVED**, no remaining findings. Reviewer inspected API/web
diff, focused tests, final full harness and actual app/provider artifacts, including final doctor.
Approval does not establish the external SDI/live-account/subscription-renewal paths named above.

Additional real card-setup proof: a separate admin-owned organization with no saved card enabled
automatic top-up through the API. Stripe TEST created a setup Checkout (200); SQL readback showed
`awaiting_card=true`, `enabled=false`. A subsequent native invalid Canadian business ID caused
profile sync 502 and committed both flags false. Completing that older real setup Checkout with
test card 4242 produced `status=complete`, `payment_status=no_payment_required`; the forwarded
webhook returned 200 and SQL/API still showed both flags false. This proves the fiscal failure
prevents a stale card-setup completion from re-enabling top-up. Evidence: `setup-real-provider.json`,
`setup-pending-failure.json`, `setup-complete-stripe.json`, `setup-completed-disabled.txt` and
`setup-completed-state.json`. No new source changes followed the final full gate or approval.
