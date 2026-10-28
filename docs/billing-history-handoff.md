# Billing history handoff

Task: first billing implementation block, authorized in chat. Branch `feat/billing-history`,
worktree `zenbree-billing-history`; organization billing/team roles integrated from `origin/main`
with merge commit `ff12493`. The original document-history commit is `b002173` (draft PR #319).

## Acceptance

- Organization admins see paginated Stripe payment documents with date, number, status,
  currency-aware net/tax/total amounts, PDF and hosted links for finalized documents.
- No customer means empty history; missing Stripe configuration or outage means an
  unavailable state. Drafts have no download links; unknown net/tax remain unknown.
- Shared live membership guard and customer-scoped cursors prevent foreign-org disclosure.
- Invoice cursor and ledger offset stay independent. Any organization admin can reach the
  existing Stripe portal, including pack-only organizations without a subscription.
- Members see the shared credit balance but cannot see documents or manage billing.

## Scope

Standalone `api/app/billing/invoices.py` and router registration, focused SQL tests;
`web/lib/billing/{api,schemas,schemas.test}.ts`, billing page/documents section and ledger
pagination links, account IT/EN messages; contracts and billing verify recipe.
No new migrations, checkout changes, dependencies or product mock scaffolding.

## Dependencies resolved

#306 organization billing merged in PR #324 (`a9c29ec`) and #308 team roles merged in
PR #320 (`0a7af6b`). The endpoint now reuses `require_org_admin` and the checkout/portal
`_stored_customer_id(user.org_id)` lookup. The draft's local role SQL and owner-slug mapping
are deleted. The merged member-only balance gate also hides the history section.

The SQL tests use signed test JWTs and a fake JWKS at the identity verification boundary;
`current_user` and its real membership query run for every request. They cover two admins,
foreign customer/org isolation, member demotion/removal, cursor ownership, organization
switches and a pack-only portal call as the second admin. Renaming an org slug does not
change its customer. No dependency overrides bypass the authorization under test.

## Checks

- Merged `./harness/init.sh all --fast`: PASS, no baseline failures.
- Focused `TEST_DATABASE_URL=<disposable localhost:55449> uv run pytest tests/test_billing_invoices.py -q`:
  9 passed with the shared real membership guard.
- Final `TEST_DATABASE_URL=<disposable localhost:55449> ./harness/init.sh all`: PASS.
  648 API tests, 733 web tests across 71 files, 59 landing tests, no skips; static checks
  and Next/Astro production builds passed. Log: `/tmp/zenbree-billing-history-final-harness.log`.
- `git diff --check`: PASS.
- Initial draft checks: full all PASS (620 API, 716 web, 59 landing tests and production builds).
  Initial artifacts remain under `~/.cache/zenbree-verify/billing-history/artifacts/billing-invoices/`.

## Real app verification

Final isolated project: `zenbree-verify-billing-history-final`, web 13020/API 18020.
Artifacts: `~/.cache/zenbree-verify/billing-history-final/artifacts/billing-invoices/`.
Current build and health/JWKS were confirmed by `bin/stack doctor`. Verified users signed in
through Better Auth, skipped onboarding and opened `/billing`.

- `empty-it.ax.txt`/`empty-it.png` and `empty-api.json`: empty history/API 200 before a customer.
- `unavailable-it.ax.txt`/`unavailable-api.json`: a disposable customer fixture without a key
  gives the unavailable UI/API 503. This setup does not represent a payment.
- `member-balance-only.ax.txt`, `member-denied-api.json`, `removed-denied-api.json`: a current
  member sees only shared balance, API 403; removal refuses the already minted JWT with 401.
- `populated-api.json`, `populated-it.ax.txt`/`.png`: 20 real Stripe test invoices, all five
  statuses, net €10.00/tax €2.20/total €12.20, finalized links, drafts without links.
- `page2-api.json`: the actual customer-scoped cursor returns the remaining one of 21 invoices.
  `foreign-cursor-api.json`: a real foreign-customer invoice cursor is refused with 400.
- `page2-it.ax.txt`, `page2-offset50-it.ax.txt`, `latest-offset50-it.ax.txt`,
  `ledger-newer-keeps-invoicecursor.ax.txt`: actual invoice older/latest and ledger older/newer
  UI links preserve each other's cursor/offset. Captures were taken after navigation settled.
  The 51 disposable ledger rows are pagination setup and do not change the credit wallet.
- `document.pdf`, `pdf-headers.txt`: the UI's real Stripe PDF URL returns HTTP 200 and a valid
  `%PDF-` document (21,730 bytes). `hosted-document.ax.txt`/`.png` captures the actual view-link
  click opening a loaded Stripe hosted invoice with TEST MODE, invoice number and €12.20.
- `populated-en-phone.ax.txt`/`.png`, `populated-phone-overflow.json`: populated English table
  at 390px has no horizontal overflow; Italian desktop document and status labels also driven.
- `second-admin-own-org-empty.ax.txt`, `second-admin-team-documents.ax.txt`/`.png`,
  `second-admin-api.json`: second admin signs in, sees their own empty org, then uses the actual
  Account menu switch to the billed org. Both admins receive exactly the same 20 invoice ids.
- `pack-only-no-subscription.txt` reads back zero subscriptions; `pack-only-portal.ax.txt`/`.png`
  proves the second admin's real management button opens the loaded Stripe TEST portal with
  invoice history and billing information. This proves access without a subscription.

The private Stripe test account supplied synthetic customer/invoice fixtures. Paid fixtures
were marked `paid_out_of_band`; the 22% tax rate was a test fixture. No card checkout, Stripe
Tax checkout, actual purchased pack, renewal, refund or fiscal issuance is claimed. The same
organization/customer/portal path used by pack purchasers was exercised without a subscription.
Real test documents and portal replace the initial draft's missing-provider proof blocker.
The isolated final stack/browser were cleaned with `bin/stack down`; artifacts are retained.
Root removed the temporary test credential file/compose override and stopped the dedicated
55449 harness database after verification. Synthetic Stripe fixture ids are retained outside
the repository for evidence.

Test credentials are protected outside the repository. No production environment files,
credentials, JWTs or generated tokens are included in commits/evidence.

## Review and remaining scope

The initial independent review requested #306/#308 integration and positive Stripe history,
PDF/hosted/cursor/pack-only portal proof before merge. Integration and real provider proof are complete. Final independent review: **APPROVED**,
no remaining findings. The reviewer checked the actual integrated diff, full-check log and
settled app/Stripe test artifacts, including the second admin/customer identity and zero
subscriptions. Publishing and merging follow existing user authorization; this approval
does not claim healthy production deployment.

SDI automation remains mandatory in the overall billing plan. This block shows Stripe payment
documents; it does not issue an Italian fiscal invoice or confirm SDI acceptance. Actual SDI
provider integration, VAT checkout/reconciliation, renewal/refund flows and credit notes are
separate billing implementation blocks, not claims of this history feature.
