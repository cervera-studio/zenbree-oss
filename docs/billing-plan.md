# Billing completion plan

Written 2026-10-06 at 1fe0fca; status updated 2026-10-15. Reference only: check the issues before planning from it. Execution status lives on GitHub; phases 1 and 2 are done, phase 3 is mostly done.

Confirmed by the owner: Italian seller, both businesses and consumers (the market is worldwide since 2026-10-07, `docs/decisions.md:24`, #355).
Mandatory owner requirement: automatic fiscal invoicing for purchases, renewals and auto top-ups. Manual invoice entry is not an acceptable normal operating workflow or launch substitute. The owner confirms they can manually issue domestic and foreign invoices through Fiscozen; this confirms existing issuance capability, not programmatic integration.
Initial invoicing entity: the owner as a sole trader, until a separate company is established. The owner confirmed ordinary VAT treatment; confirm the fiscal-provider regime mapping with the accountant. Establishing the new company is not a prerequisite for initial invoicing.

Issuer and recipient data (names, tax identifiers, address, SDI recipient code) live outside the repo, with the owner. Configure them in Stripe and the fiscal provider through their secure account settings; never put them in Git. Keep the issuer's own incoming-invoice recipient code distinct from each buyer's recipient code/PEC collected for outgoing Zenbree invoices. Confirm PEC/contact details, invoice numbering and any pension-contribution treatment with the accountant.

## Acceptance

- An organization admin can understand the balance, buy credits, manage the subscription and payment method, edit billing details, and download historical documents.
- Credit movements and monetary payments have separate histories; neither a credit refund nor a reservation is presented as a refunded card payment.
- Italian business and consumer purchases collect the required fiscal details. Consumer prices include applicable taxes; business prices clearly identify net and gross amounts.
- Packs, subscriptions, renewals, upgrades and automatic top-ups produce one coherent payment/document trail. Failed, pending and refunded payments remain visible.
- Eligible fiscal documents are issued and transmitted automatically, with their references and status reconciled to the originating Stripe documents. Support handles exceptions; customers and the owner do not manually re-enter each sale. Refunds produce the required linked corrective documents automatically under the approved fiscal policy.
- Tax treatment, electronic invoicing, credit notes, numbering and retention are approved for the seller's actual regime; SDI failures are actionable and recoverable.
- Duplicate or reordered provider events, application restarts and organization switching cannot double-charge, double-grant credits or expose another organization's documents.
- Existing customers, purchased credits and subscriptions survive migration. Real Stripe test-mode journeys and fiscal-provider test journeys have recorded evidence before launch.

## Scope and dependencies

This task changes only `docs/billing-plan.md`. Implementation scope is proposed below. Reuse #111 for electronic invoicing (still open). The organization prerequisites (#305, #306, #308, #316) are closed.

Implementation will touch `api/app/billing/**`, new `api/db/<timestamp>_*.sql`, billing tests, `web/app/(app)/billing/**`, `web/lib/billing/**`, relevant IT/EN messages and navigation, public pricing/terms, `docs/contracts-m1.md`, `docs/decisions.md`, and `.claude/skills/verify/features/billing.md`. Add admin, worker/config and deployment files only for the relevant phase.

## What exists and what is missing

| Area | Observed implementation | Remaining work |
|---|---|---|
| Payments | Stripe hosted Checkout, packs, monthly plans, customer portal (also for pack-only organizations, #319) | Validate live configuration, show pending/failure states |
| VAT | Checkout collects address and tax IDs; automatic tax enabled. Fiscal profile (business/individual, fiscal code, recipient code, PEC, Stripe sync) done: `api/app/billing/profile.py`, #332 | Consumer pricing (open owner decision), VAT verification policy, seller registrations and country coverage |
| Credits | Ledger, holds, renewal expiry, permanent packs, automatic top-up. Plan vs purchased credits and transaction links done (#498/#348). Upgrades, downgrades and cancellation done (#417/#546, #538/#665) | Prove plan changes in Stripe test mode (#541) |
| History | Done: `GET /v1/billing/invoices` (`api/app/billing/invoices.py`, #319) with `invoices-section.tsx`; see `docs/billing-history-handoff.md` | Tax totals, credit notes, refunds in the history |
| Refunds | `charge.refunded` updates purchases; credit removal is manual | Gross/net correctness, event-order recovery, explicit reviewed credit adjustment and fiscal credit note |
| Reliability | Signed webhook, transactional event deduplication, unique purchase references | Reconciliation and operational handling for unmapped, failed or reordered events |
| Electronic invoicing | Open issue #111 | Provider, fiscal data, transmission, rejection recovery, conservation and backfill |
| Verification | Billing recipe covers local UI and error paths | Successful Stripe and fiscal-provider journeys are explicitly skipped in the existing recipe |

Code evidence: `api/app/billing/routes.py`, `auto_topup.py`, `web/app/(app)/billing/page.tsx`, `plans-section.tsx`, `transactions-section.tsx`, billing schemas and the verify recipe. Production Stripe/SDI configuration has not been inspected.

One concrete gap remains: checkout success is inferred from the `?checkout=success` URL parameter (`web/app/(app)/billing/page.tsx:48`). A redirect must never be proof of payment or of granted credits.

## Proposed product and ownership

Keep the existing `/billing` destination and settings components. Present four sections or tabs using existing navigation patterns:

1. **Overview:** total available, plan allowance remaining, purchased credits, held credits, reset date, current plan, renewal/cancellation and payment problems.
2. **Usage:** existing ledger with date/type filters, signed credits, balance and authorized links to a job/chat/list where the stored references permit them.
3. **Payments and documents:** date, document number, purpose, status, net, tax, gross, currency, refunded amount, hosted payment link and downloadable document. Include packs, renewals, upgrades and auto top-ups. Paginate independently of usage.
4. **Billing details:** business/consumer selector, legal name or personal name, country and address, invoice email, VAT ID where applicable, Italian fiscal code and recipient code/PEC as applicable. Card details and subscription changes stay in Stripe's portal.

Organization admins manage billing. Members have no access to fiscal details, invoices or payment actions; backend checks are authoritative and membership must be current. Keep application super-admin support access distinct and audited. A consumer still has an organization wallet: legal purchaser type is a fiscal attribute, not a team role.

Stripe owns payment/subscription facts and tax calculations. Existing SQL owns credits. The chosen fiscal provider owns fiscal issuance/transmission and conservation. Keep document snapshots immutable after issuance; profile edits affect future documents. Never store full card details or print fiscal data in logs.

## Fiscal and pricing decisions

The 2026-10-02 B2B-only pricing decision (`docs/decisions.md:46`: displayed prices exclude VAT) is still in force, and the landing says "Prezzi IVA esclusa", while the fiscal profile already accepts `individual` buyers. Consumers are sold to (confirmed above). **Open owner decision:** how consumer prices show (VAT included for them, or one VAT-excluded list), to be recorded in `docs/decisions.md`.

Decisions that landed after this plan and that it must not contradict: the credit repricing (`docs/decisions.md:56`, 1 credit = 1 verified email, Starter €29 = 500 credits), the plan-change rules (#417, `docs/decisions.md:58`: upgrade at once, downgrade at renewal) and the worldwide market (`docs/decisions.md:24`, #355).

- Configure the initial Stripe account and fiscal provider for the owner's registered issuer details and confirmed ordinary VAT regime. Use those details consistently in checkout, fiscal documents, receipt emails and seller information in terms.
- When the new company is established, plan a separate issuer transition with the accountant and Stripe/provider: effective date, account ownership, recurring subscriptions, numbering and historical access. Preserve the initial issuer on its historical documents; never rewrite them under the new company. Do not build multiple-issuer infrastructure before this transition is needed.
- Proposal: preserve existing net EUR prices for businesses; show an explicit consumer view with the applicable gross price. Public pricing must visibly support consumers, with country context rather than silently applying Italian VAT worldwide. For example, an ordinary Italian 22% case turns EUR 29 net into EUR 35.38 gross. Keep calculation in Stripe Tax; the example is not a hard-coded rule.
- The seller's ordinary VAT regime is confirmed. Identify the product tax code, tax registrations and countries served. Confirm EU consumer digital-service treatment, eligibility for any small-seller threshold, OSS registration, location evidence and reporting with the accountant before enabling those markets. Extra-EU treatment also needs a country-specific policy.
- A tax ID with the correct format is not proof of verified eligibility: Stripe Tax can apply reverse charge despite the government verification result. Persist verification state and decide handling for pending/unavailable/invalid IDs before zero-tax invoicing. Never mark a customer exempt merely because they selected business.
- Collect fiscal information before first charge, including off-session charges. Define correction of an existing incomplete profile, and require confirmation of purchaser changes before another charge.
- Confirm consumer terms: recurring charge consent, automatic top-up consent, cancellation, refunds/withdrawal, and any conditions for immediate service delivery. Review these alongside tax rules rather than assuming the existing B2B terms suffice.

Sources: [consumer total prices](https://europa.eu/youreurope/citizens/consumers/shopping/pricing-payments/indexamp_en.htm), [Stripe tax ID validation](https://docs.stripe.com/tax/invoicing/tax-ids), [EU OSS](https://europa.eu/youreurope/business/finance-and-tax/vat/one-stop-shop/indexamp_en.htm), [Stripe EU tax setup](https://docs.stripe.com/tax/supported-countries/european-union).

## Implementation sequence

| Phase | Status | Deliverable and acceptance | Proposed scope | Dependencies |
|---|---|---|---|---|
| 0. Close decisions | Open (owner/accountant decisions; consumer pricing, provider) | Use the owner as initial issuer under the confirmed ordinary VAT regime; confirm registered details, markets, net/gross commercial policy, refund policy, fiscal numbering and provider. Inventory Stripe products/prices/tax registrations/portal/webhook API version without exporting secrets. Record decisions. | Decisions and #111 handoff | Owner/accountant |
| 1. Fiscal profile and pricing | Done (#332, #319) | Persist organization billing profile; validate conditional business/consumer fields; synchronize Stripe customer, address and tax IDs with recoverable failures. Consumer display and checkout agree. Pack-only buyer can open portal. | Billing API/UI, new migration, public pricing/messages/terms/contracts | #306 and role enforcement from #308; phase 0 |
| 2. Payment and document history | Done (#319, `docs/billing-history-handoff.md`) | Organization-scoped, paginated history with net/tax/gross/refunds and links. Cover unpaid/void invoices and customer without history; backfill existing Stripe customers safely. | Billing API/UI/schemas, new migration, existing worker if sync needed | Phase 1 |
| 3. Credit clarity and lifecycle | Mostly done (#498, #417, #546, #665); Stripe test-mode proof open (#541) | Explain available/held/expiring credits; improve usage labels and references. Pending checkout waits for server confirmation. Failed renewals, card recovery, upgrades, scheduled downgrades and cancellation behave as documented. | Existing credit API/ledger/UI, notifications only where needed | #306; phases 1–2 |
| 4. SDI and credit notes | Open (#111) | Complete #111 in test mode, reconcile documents and expose processing/accepted/rejected/unavailable states. One fiscal document per source document, recoverable errors, no duplicate issuance. | Provider configuration first; minimal document status/API/admin additions; adapter/task only if required | Fiscal decisions and provider access; phases 1–2 |
| 5. Reliability and launch | Open | Reconcile missed events, gross/net refund accounting, credit adjustments, alerting and historical imports. Capture full test evidence, CI and release runbook. | Billing/admin/worker/tests/verify/ops within explicit phase scope | Phases 1–4 |

Each phase becomes a reviewable PR with its own Acceptance/Scope/Depends on. This document is a design plan, not a duplicate status backlog; execution status remains on GitHub.

### History data and API

Done (#319): see `docs/billing-history-handoff.md` and `docs/billing-profile-handoff.md`. What still applies from the original design: keep only the SQL projection needed for SDI status, fiscal snapshots or reconciliation, keyed by `(provider, external_document_id)` with organization ownership; never expose drafts as finalized invoices; fetch authorized document links on demand; imported documents must never grant credits again.

### SDI: first try an existing connector

The owner confirms existing Fiscozen supports manual domestic and foreign invoicing. Fiscozen's [official FAQ](https://www.fiscozen.it/faq) includes electronic invoicing; no official public API or Stripe connector was found in this research. Check with its support for a supported integration that can create and transmit documents, retrieve documents/status and handle credit notes without manual entry. Use Fiscozen for automated issuance only if that capability is confirmed and proven end to end. Manual issuance is an exception-recovery option, not an initial release plan. Do not build automation against private web-app endpoints or browser sessions.

Revolut's [invoice documentation](https://help.revolut.com/it-IT/business/help/receiving-payments/invoices/can-i-provide-my-clients-with-invoices/) describes customer invoices but does not establish Italian SDI transmission. Its [Fatture in Cloud integration](https://help.revolut.com/en-IT/help/integrating-with-external-apps/using-the-fatture-in-cloud-integration/business/) is a separate integration. Do not select Revolut's invoice feature as the fiscal transmission solution without explicit confirmation of FatturaPA and SDI support.

If the existing service cannot support automation, evaluate A-Cube's Stripe app: official documentation describes automatic sending, numbering configuration, consumer fiscal-code metadata and test mode. Its app supports Stripe production and test environments, but currently not Stripe sandbox. This is a candidate, not a purchased or approved provider. Confirm fees, conservation terms, API/status access, document download, partial refunds, tax mappings, EU consumer flows and historical imports in the trial.

Implementation research: the [customer mapping](https://docs.acubeapi.com/documentation/connectors/stripe/customer_fields) reads buyer recipient code from customer metadata `codice_destinatario` and consumer fiscal code from `fiscal_code`. Buyer fields must never be filled with the issuer's own recipient code. The [credit-note flow](https://docs.acubeapi.com/documentation/connectors/stripe/credit_notes) automatically imports issued Stripe Credit Notes for Stripe invoices, while its payment-only refund flow describes manual form steps. Use the invoice-based integration already present in Zenbree and prove automatic issuance/transmission of the resulting corrective document; do not assume `charge.refunded` alone satisfies the requirement.

Use one transmission owner: the connector OR our task, never both. The connector must expose enough status/document data for acceptance; otherwise use the chosen provider's API through one small Celery task. Keep the payment webhook transaction independent from SDI availability, persist dispatch state, and reconcile after restarts. The issuance trigger must follow fiscal deadlines; do not assume paid-only issuance is valid for every case.

The accountant/provider decides numbering, issuer regime, Natura/bollo mappings, foreign reporting, consumer documents and correction rules. Do not regenerate XML or calculate fiscal mappings from scratch. Rejected documents require a controlled correction/resubmission flow; delivered documents require the proper corrective document. A Stripe refund, fiscal credit note and credit-ledger adjustment are distinct actions with explicit references and idempotency.

Sources: [A-Cube connector and test environments](https://docs.acubeapi.com/documentation/connectors/stripe), [A-Cube settings and B2C metadata](https://docs.acubeapi.com/documentation/connectors/stripe/settings), [Agenzia delle Entrate electronic invoicing guide](https://www1.agenziaentrate.gov.it/web_app_entrate/fatturazione_elettronica.html/1000). Provider capability statements are documented claims; integration has not been trialled.

### Payment correctness and operations

- Reuse current signed webhook and SQL uniqueness. Route events by the authoritative organization/customer mapping, including historical user-based metadata after #306.
- Reconcile event reordering and missed notifications by retrieving canonical provider state. Current unmapped invoice paths log and return; marking the enclosing event processed must not make an uncredited payment disappear from recovery.
- Audit refund accounting: `purchases.amount_cents` is net while `charge.amount_refunded` is a gross payment amount. Derive the actual tax/revenue correction from source records, including partial refunds, rather than subtracting gross from net. Reconcile refunds arriving before their payment/invoice mapping.
- Preserve the existing manual credit clawback until a reviewed refund policy is defined. Support records monetary refund, affected unused credits and reason; spent credits must not silently cause an invalid balance. Cover chargebacks/disputes with an explicit operational policy.
- Keep existing pack permanence and plan expiry decisions. Existing past-due rollover tolerance is a documented shortcut: confirm a grace/access policy rather than silently changing it.
- Retry ambiguous provider outcomes with stable references and canonical-state checks. Bound retries, surface unresolved failures to support, and keep recovery possible without issuing duplicate payments/documents.
- Retain only required fiscal data; document access, retention and deletion treatment. Tax/SDI failure alerts must identify records without exposing names, addresses or tax IDs in logs.

## Verification and release evidence

Use the repository's existing tests and `.claude/skills/verify/`; add focused regressions for the actual paths. Run full `./harness/init.sh all` on a disposable database before implementation merge. New SQL migrations must build a fresh database and migrate an existing test schema without changing applied checksums.

Drive Stripe test mode with a local test webhook and the actual browser. Prove: Italian business and consumer; EU verified/invalid/pending VAT cases; EU consumer tax policy; configured extra-EU case; first pack, first subscription, renewal, upgrade, downgrade, cancellation; auto top-up success/decline/authentication required; redirect before webhook; duplicate/reordered event; provider timeout; partial/full refund and credit note. Read balances and document records back after each money-changing journey.

Prove two organizations, switching, non-admin and removed-member denial, authorized document downloads, empty/unavailable history, pagination, IT/EN, desktop/phone accessibility. In the fiscal provider's test environment prove successful transmission, rejection, correction, unavailable delivery and duplicate recovery; simulated fiscal transmission is not proof of live SDI acceptance.

Launch evidence includes screenshots, database readback, event/document references, commands, skips, alert/recovery runbook and self-review against `harness/CHECKPOINTS.md`. Do not claim independent approval without an actual reviewer. Recheck #111's old live-first blocker: test-mode investigation can proceed before live activation; production transmission requires explicit configuration and fiscal readiness. Historical issued documents must not be resent automatically.
