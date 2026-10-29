-- #499: the address the Stripe customer was last given. The sweep (app.billing.customer_email) pushes the
-- creator's login email to Stripe whenever it differs, so invoices follow a changed email (#497).
-- NULL on the rows that exist: Stripe has the address of the day the customer was created, which nobody
-- recorded, so the first sweep sends the current one once and fills the column.
ALTER TABLE billing_customers ADD COLUMN email TEXT;
