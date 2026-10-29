---
name: credit-holds
description: Model-check the credit-hold lifecycle (reserve, grow, per-row charges, settle, the reaper, campaign enrolment, bounce refunds) with TLA+ in every order of steps. Use it when a change touches a credit hold, a row charge, the wallet or a bounce refund in api/app/billing, api/app/agent/turn_credits.py, a holder that opens a Tab (automations, watchlists, scheduled searches, row phone, AI columns), the reaper or a credit SQL function in api/db. Tests cover the orders someone wrote down; this checks them all.
---

# Model-check the credit holds

`api/formal/credit_holds/check.sh` runs TLC, the TLA+ model checker, on `CreditHolds.tla`: a model of how Zenbree takes, grows, charges and closes a credit hold while the reaper, campaign enrolment and bounce refunds act on the same hold. [`api/formal/credit_holds/README.md`](../../../api/formal/credit_holds/README.md) holds what the check proves, the open findings, the bounds, the coverage record, the action table and the mutants.

The model does not read the code. TLC checks a change only after you copy the change into `CreditHolds.tla`.

## Check a change

1. Run `api/formal/credit_holds/check.sh`. Docker (OrbStack) must run.
2. If it prints `DRIFT` lines, read the diff of each listed file against the sync commit that `check.sh` names (`git diff <sync> -- <file>`). Ignore a change that touches no hold, row charge, wallet, row completeness or bounce. The README's coverage record says which functions of each file matter. If you ignore every listed change, go to the last item of step 6.
3. For each change that matters, find its actions in the README's action table, and copy the change into those actions. Change nothing else. A dropped guard drops its conjunct, and a new check adds one. The functions that the changed code calls keep their own guards. Keep one action per committed transaction.
4. Run `api/formal/credit_holds/check.sh --all`. It takes about 2 minutes.
5. If a property fails, read the trace as the README's section "Read a failure" shows. When in doubt, it is a real bug until you show the line of code that stops the trace. Report a real bug with its steps in the code's words. Do not weaken the model to make it pass, and do not change product code unless the user asks.
6. If the change lands and every property passes, finish the update:
	- Tag a new guard `[Mn]`, and add its mutant to `mutants` in `check.sh`.
	- Add an invariant for a new promise, list it in `CreditHolds.cfg`, and add a mutant that breaks it.
	- Update the README's coverage record and action table, and `covered` in `check.sh`.
	- Set `sync` in `check.sh`, and the sync commit in the README, to the commit you checked. Move them only after you confirm that the model describes the code at that commit.

If you reject a code change because of a real bug, also undo its copy in the model.

## Exit codes of check.sh

- `0`: every run ended as expected, and no covered file changed since the sync commit.
- `1`: a run did not end as expected. The script prints the reason and the TLC output.
- `2`: the check cannot run: no Docker daemon, `tla2tools.jar` cannot download or fails its checksum, or the sync commit is not in this clone.
- `3`: every run ended as expected, but covered code changed since the sync commit. Do steps 2 to 6.

The README's section "Read the output" explains each `PASS`, `KNOWN`, `FAIL` and `DRIFT` line.
