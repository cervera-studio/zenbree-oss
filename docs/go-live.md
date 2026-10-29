# Go-live checklist

Steps that only the owner can do on an external dashboard, and that the code cannot check.
Tick each one on the live account before go-live.

## Stripe

- [ ] Live account, Billing > Customer portal > Cancellations: "Cancel at end of billing period", proration off.
  The sandbox already is (`bpc_1ULjnRIepLn1nrMHWtuqdbio`, checked 2026-10-22, #972). An immediate cancel with proration would let
  an org admin spend the plan credits, cancel, and be granted the full allowance again from the customer balance
  (`_handle_invoice_paid` ignores `amount_paid`). If the live setting differs, reopen #972. Pinning a dedicated
  `billing_portal.Configuration` in code is part of #541.
