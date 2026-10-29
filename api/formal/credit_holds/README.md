# Credit-hold model

`CreditHolds.tla` is a TLA+ model of how Zenbree takes, grows, charges and closes a credit hold while other actors act on the same hold. TLC, the TLA+ model checker, runs every order of those steps inside the bounds in `CreditHolds.cfg` and checks the invariants below in every state it reaches. A pass covers the model within its bounds, not the whole system. The `/credit-holds` skill (`.claude/skills/credit-holds/SKILL.md`) says when and how to run it.

`check.sh` downloads `tla2tools.jar` v1.7.4 once into `~/.cache/zenbree-tla/`, checks its sha256, and runs TLC in `eclipse-temurin:21-jre`, pinned by digest. TLC keeps its state files inside the container, so nothing lands in the repo.

## What the check proves

Each invariant guards against a harm to a paying user. Three of them, `TurnEndRefundsUnfinished`, `ChargeOnLiveHold` and `NoPendingOnClosedHold`, hold by construction: one action writes both sides of each, so no order of steps can break them. They fail only when a change to that action breaks them: a code change you copy into the model, or the mutant `M3`, `M8`, `M10` or `M13`. A stale read inside `refund_unfinished` is out of the model.

| Invariant | Plain statement | Harm when it breaks |
|---|---|---|
| `WalletNeverNegative` | The wallet never goes below zero. | The organization spends credits it never bought. |
| `CreditsConserved` | Wallet + reserved holds + what closed holds kept = what came in − what went out, bounce refunds included. | Credits appear or vanish without a ledger line. |
| `CapRespected` | The project's hard monthly cap (#307) is never passed. | A client is billed past the cap the admin set. |
| `ChargesWithinHold` | A holder never bills more than its hold. | The user reads cells that `settle_hold`'s `LEAST` let them have for free. |
| `KeptIsWhatCharged` | A closed hold kept exactly the credits of its charges, less those refunded while it was reserved. | Unpaid cells, a double charge, or a refund that the hold also kept. |
| `BounceRefundOnce` | A bounce gives its credit back once: by `increment_credits` after the close, or by the close itself while the hold is reserved. | The user gets one bounced email paid back twice. |
| `TurnEndRefundsUnfinished` | At a live turn's end, a held charge is refunded exactly when its row is still unfinished (#45, #416). | The user pays for rows that miss a required column, or gets back credits for complete rows. |
| `ChargeOnLiveHold` | Every charge was written while its hold was reserved. | A cell lands that no hold pays for. |
| `NoPendingOnClosedHold` | A held charge exists only on a reserved hold. | Cells stay hidden for good (`PENDING_KEYS_SQL`). |
| `BounceRefundHonored` | No hard bounce refunds nothing while its row still has a final, unrefunded charge for the address (#747). The model treats every bounce as one that qualifies for a refund: the verifier's re-check calls the address invalid, within `REFUND_DAYS`. | The user pays for an email that never arrived. **Fails today: see Open findings.** |

## Read the output

`check.sh` prints one line for each run:

- `DRIFT`: covered files changed since the sync commit, or are new and untracked. The model may no longer describe them. The script exits 3 when every run passes but drift remains.
- `FAIL holder files not in covered`: a file opens, closes or finalizes a hold, and `covered` in `check.sh` does not list it. Model it, then add it.
- `PASS CreditHolds.cfg`: every invariant holds within the bounds (5359892 distinct states at the sync commit).
- `FAIL CreditHolds.cfg: a property broke`: TLC found a trace. Read it as the next section shows.
- `FAIL ... TLC did not check the model`: a parse error or a Docker failure. The full TLC output follows, with the line and column of a parse error.
- `KNOWN`: an open finding still fails by its recorded trace.
- `FAIL finding ...: the shortest trace is now ...`: the finding still fails, but by another path. Read the trace. A new path can be a new bug.
- `FAIL ... now holds. The finding is fixed`: list the invariant in `CreditHolds.cfg`, and remove its line from `findings` in `check.sh`.
- `PASS Mn`: the mutant broke the model, and the invariant named for it caught the break.
- `FAIL Mn ... still holds`: that invariant no longer guards the code the mutant names.
- `FAIL Mn: the substitution changed N lines, not 1`: an edit changed the text of a tagged line. Update that mutant's substitution in `check.sh`. If the guard moved to another line, move the tag with it.
- `SKIP mutants`: `CreditHolds.cfg` failed, so a mutant would read as caught by a property that had already broken.

`check.sh` takes about 1 min 45 s, and `check.sh --all` about 2 min. TLC runs under the heavy-job lock (`harness/heavy-lock.sh`), so it waits while `init.sh` or another heavy job runs.

## Read a failure

A failure prints the TLC trace: a numbered list of states, each headed by the action that led to it, such as `<TurnDeliver line N, col M ...>`. To read it:

1. Write each action as the code step it models, from the action table below.
2. Note the variables that changed between states: `status`, `amount`, `kept`, `wallet`, `bounceBack`, `bounce`, and the `state`, `why`, `refundOpen` and `endRow` of each record in `charges`.
3. Find the invariant on the first `Error:` line, and its plain statement in the table above.

Then decide whether the trace is a real bug or a modeling error:

- **Real bug.** Each step in the trace can happen in the code in that order, and the final state breaks the plain statement.
- **Modeling error.** A step cannot happen in the code. Typical causes: the model splits one transaction into two actions, misses a lock the code takes, misses a guard the code checks, or allows a bound the code never reaches. Fix the model to match the code, and cite the code line in a comment on the action.

## Open findings

### A bounce while the hold is reserved refunds nothing (#1217)

`BounceRefundHonored` fails. TLC's shortest trace, in the code's words:

1. An automation run opens its hold with `Tab.open` (`automations/runner.py`).
2. A step records a charge on a row that holds every required column, so `row_charges.charge` writes it `charged` at once. The hold stays reserved for the next steps.
3. The campaign sends the address, and it bounces hard while the run still holds the hold. `automations/steps.py` enrols people during the run. `outreach/bounces.record` marks the send bounced hard (`_MARK`), then calls `bounce_refund.refund` → `row_charges.refund_bounced`, which skips a charge whose hold is `reserved`. Nothing is refunded.
4. `bounces.record` leaves out a send that already bounced hard, so nothing retries the refund. The hold closes later and keeps the charge.

A chat turn's hold has the same gap: a charge on a complete row is final at once, and a user can enroll the row and send while the turn runs. The window lasts from the charge until the hold closes, at most `RESERVATION_STALE_AFTER_MINUTES` (20) plus a reaper tick.

The guard exists because a refund through `increment_credits` while the hold is reserved would be paid twice: `settle_hold` does not keep a refunded charge. Mutant `M5` shows that double refund, and `BounceRefundOnce` catches it.

A candidate fix refunds a reserved hold's charge without `increment_credits`, and lets `settle_hold` give the credit back. On a copy of the model with that fix, every other invariant holds, but `BounceRefundHonored` still fails by a longer path:

1. A holder bills the same row twice, so the row has two final charges for its address.
2. Two campaign sends of the address bounce at the same time. Both `refund_bounced` statements pick the newest charge, and the second waits on `FOR UPDATE OF c, r`.
3. The first refunds the newest charge, and `take_back` clears the row's email cell. The second statement re-checks the locked rows and moves to the older charge, but the row fails the email check now, so it refunds nothing. The older charge stays paid.

In the code, the second statement's fallback to the older charge is a reading of the plan (`Limit` → `LockRows` → `Sort`), not a test. The model does not show it: it has no email cell, and its second `BounceApply` stops at the refunded charge. Both reach the same end. That path is real only if a row can carry two final charges that bought its email. The schema allows it: `row_charges` has no unique key on a row and a key, and `charge` writes one record for each call. Whether a holder bills an email that the row already holds is the question to settle when you fix #1217. Found on 2026-10-23 at the sync commit.

## Bounds and assumptions

`CreditHolds.cfg` sets 2 rows, a wallet of 3, a hard cap of 3, 1 deposit, 1 withdrawal, at most 3 charges, quotes of 1 to 2 credits, at most 2 parallel calls in the turn, and at most 2 bounced sends over all rows. Each charge is 1 credit. The cap is below the wallet plus the deposit, so the cap binds before the wallet does.

The model assumes:

- **The reaper fires at any time.** The 20-minute threshold is not modeled, so the reaper can close a hold under a live holder. This includes every order the threshold allows.
- **One organization, one project, one month.** Two organizations never share a hold. The month boundary (`carried` in `credits.grow` and `grow_reservation`) is not modeled.
- **Two holds.** One chat turn's hold and one stored-hold holder's hold. Scheduled searches, watchlists and row phone clicks record and close in one transaction (`RunRecord` with `closeNow` true). Automations and AI columns record in one transaction per step or cell and close in a later one (`closeNow` false, then `RunClose`).
- **Python reads before the locked recheck are free.** `credits._room` and the `carried` read pick an amount, and the SQL function then rechecks under the lock. The model lets the amount be any value and keeps only the SQL checks.
- **A delivery is one transaction.** `AgentLoop._deliver` locks the hold, stores rows and records charges in one transaction, so `HoldGone` rolls back all of it.
- **Every bounce qualifies for a refund.** The verifier's re-check and `REFUND_DAYS` are not modeled.
- **Every charge bought the row's email.** The code refunds only a charge with `'email' = ANY(c.keys)` on a row whose email cell still passes `verified_email_sql` and `_bought('email')`. The model has no cells, and `take_back` is not modeled.
- **Deposits and withdrawals are abstract.** `increment_credits` (Stripe checkout, renewal, auto top-up, admin) adds 1. `deduct_credits` (admin, plan expiry) and `pay_plan_expiry_owed` take 1 only when the wallet holds it.

Left out: deadlocks (a deadlock rolls a transaction back, which the model already allows as a kill), `refunds_by_call` and the turn's notification counts, `free_rows` daily holds, LLM metering, vendor `paid_call` costs, the `jobs.credits_spent` cache, the migration backfill in 20261013183923, and `outreach_hold`.

## Coverage record

Sync commit: `initial`, also `sync` in `check.sh`. This resolves to the root commit of the public snapshot, so no tag is required. Use a full clone: a shallow clone cannot identify this baseline reliably. The covered implementation matches the model's original baseline. `check.sh` fails when the script and this record differ. At this commit, the model describes the code below. `covered` in `check.sh` lists the same Python files. A migration in `api/db` counts as covered when it names a function or table in `covered_sql`, so a new migration that redefines `settle_hold` is drift, and an unrelated one is not.

| Code | Where | Model |
|---|---|---|
| `reserve_credits`, `release_reservation`, `settle_reservation_with_slack`, `increment_credits`, `deduct_credits` | `api/db/20261006110243_organization_credits.sql` | `CanReserve`, `TakeHold`, `Close`, `Deposit`, `Withdraw` |
| `reserve_project_credits`, `project_cap_left` | `api/db/20261006134459_project_clients.sql` | `CanReserve` |
| `project_month_spend` | `api/db/20261021095255_bounce_message_ids.sql` | `ProjectSpend` |
| `grow_reservation` | `api/db/20261013183818_grow_reservation.sql` | `GrowOK`, `TurnGrow` |
| `settle_hold`, `reap_stale_reservations_ids` | `api/db/20261013183923_row_charges_hold.sql` | `Close`, `Reap` |
| `pay_plan_expiry_owed` | `api/db/20261016170605_plan_expiry_owed.sql` | `Withdraw` |
| `reserve`, `grow`, `live`, `close`, `kept` | `api/app/billing/credits.py` | `TurnOpen`, `RunOpen`, `TurnGrow`, `Close` |
| `Tab.open`, `Tab.cover`, `Tab.lock`, `Tab.record`, `Tab.refund_unfinished`, `Tab.close`, `close_stored` | `api/app/billing/tab.py` | `TurnOpen`, `TurnGrow`, `TurnDeliver`, `RunRecord`, `TurnFinish`, `RunClose` |
| `charge`, `refund_unfinished`, `finalize_hold`, `finalize`, `refund_bounced` | `api/app/billing/row_charges.py` | `NewCharges`, `RefundUnfinished`, `RunFinalizeHold`, `Enroll`, `BounceRead`, `BounceApply` |
| `UNFINISHED_SQL` | `api/app/lists/rows.py` | `finished` |
| `refund` | `api/app/billing/bounce_refund.py` | `BounceApply` |
| `take_back` | `api/app/lists/take_back.py` | `BounceApply` |
| `verified_email_sql` | `api/app/lists/lookups.py` | `Sendable`, `BounceCandidates` |
| `record`, `_MARK` (one hard bounce per send) | `api/app/outreach/bounces.py` | `BounceRead` |
| plan expiry `deduct_credits` | `api/app/billing/plan_period.py` | `Withdraw` |
| `TurnCredits.reserve`, `_hold`, `cover`, `lock`, `record`, `finish` | `api/app/agent/turn_credits.py` | `TurnOpen`, `TurnGrow`, `TurnPreflight`, `TurnDeliver`, `TurnFinish` |
| `run` (`credits.reserve()`), `_preflight`, `_run_parallel_searches`, `_deliver`, `_finish_tool`, `_end`, `_finish_credits` | `api/app/agent/loop.py` | `TurnOpen`, `TurnPreflight`, `TurnDeliver`, `TurnFinish`, `TurnDie` |
| `deliver` | `api/app/lists/delivery.py` | `TurnDeliver`, `RunRecord` |
| `hold`, `lock`, `deliver`, `close` | `api/app/scheduling/run_credits.py` | `RunOpen`, `RunRecord`, `RunClose` |
| `run_credits.hold`, `lock`, `deliver`, `close`; `_reap` | `api/app/scheduling/runner.py` | `RunOpen`, `RunRecord` (`closeNow` true), `RunClose` |
| the run's `Tab.open`, `lock`, `record`, `close` | `api/app/watchlists/runner.py`, `api/app/lists/row_phone.py` | `RunOpen`, `RunRecord` (`closeNow` true) |
| `Tab.open`, `_write_cell`, `_Run.finish` | `api/app/lists/ai_columns.py` | `RunOpen`, `RunRecord` (`closeNow` false), `RunClose` |
| `guarded` | `api/app/automations/run.py` | `RunRecord`, `RunFinalizeHold` |
| the run's `Tab.open` and finish | `api/app/automations/runner.py` | `RunOpen`, `RunClose` |
| `tab.record`, `finalize`, `apply_emails` | `api/app/automations/steps.py` | `RunRecord`, `Enroll` |
| `finalize_hold` call | `api/app/automations/instantly_step.py` | `RunFinalizeHold` |
| `close_stored` calls | `api/app/automations/service.py`, `api/app/watchlists/service.py` | `RunClose` |
| `finalize` calls | `api/app/lists/bulk_routes.py`, `api/app/outreach/drafts.py`, `api/app/watchlists/enroll.py` | `Enroll` |
| `reap_stale_reservations`, `RESERVATION_STALE_AFTER_MINUTES` | `api/app/worker.py` | `Reap` |

## Action table

`Tab.lock` (`credits.live`, `FOR UPDATE`) guards `TurnDeliver`, `TurnFinish`, `RunRecord` and `RunFinalizeHold`. In each of them, `Reserved(h)` is that check, so a change to `credits.live` reaches all four.

| Action | Code step | Transaction |
|---|---|---|
| `TurnOpen(a)` | `AgentLoop.run` → `TurnCredits.reserve` → `_hold` → `Tab.open` → `reserve_project_credits` | one |
| `TurnGrow(g)` | `TurnCredits._hold` → `Tab.cover` → `grow_reservation` | one |
| `TurnPreflight(q)` | `AgentLoop._preflight`: `credits.cover(planned_cost + quote)` | none (Python only) |
| `TurnDeliver(k, S, fin)` | `AgentLoop._deliver`: `TurnCredits.lock`, `delivery.deliver` stores rows, `Tab.record` writes the charges | one; `HoldGone` rolls it back |
| `TurnFinish` | `AgentLoop._end` → `_finish_credits` → `TurnCredits.finish`: `refund_unfinished`, then `settle_hold` | one |
| `TurnDie`, `RunDie` | a kill: hard time limit, deploy, OOM | none |
| `RunOpen(q)` | `Tab.open` with the run's quote | one |
| `RunRecord(S, fin, closeNow)` | `Tab.lock`, `Tab.record`; with `closeNow`, also `Tab.close` (scheduled search, watchlist, row phone) | one; `HoldGone` rolls it back |
| `RunFinalizeHold` | `instantly_step`: `row_charges.finalize_hold` in `guarded` | one |
| `RunClose` | `close_stored`, an automation's finish or `_Run.finish` → `settle_hold` | one |
| `Reap(h)` | `reap_stale_reservations` → `settle_hold` | one per hold |
| `Enroll(r)` | `row_charges.finalize` from bulk enrolment, drafts, watchlists, automations | one |
| `EditRow(r)` | a hand edit or another call fills or clears a required column | one |
| `Deposit`, `Withdraw` | `increment_credits`; `deduct_credits` or `pay_plan_expiry_owed` | one |
| `BounceRead(r)` | a send bounced; `refund_bounced`'s subquery reads `h.status` from its snapshot | first half |
| `BounceApply(r, k)` | `FOR UPDATE OF c, r` re-checks the charge, refund, `take_back`, `increment_credits` | second half |

## Mutants

| Id | Invariant that catches it | Code it stands for |
|---|---|---|
| `M1` | `WalletNeverNegative` | `grow_reservation` without `credits_remaining >= p_amount` |
| `M2` | `CapRespected` | `grow_reservation` without the hard cap check |
| `M3` | `KeptIsWhatCharged`, `ChargeOnLiveHold` | a delivery without `TurnCredits.lock` and `Tab.lock` |
| `M4` | `KeptIsWhatCharged` | `settle_hold` without `state <> 'refunded'` |
| `M5` | `BounceRefundOnce` | `refund_bounced` without `h.status IS DISTINCT FROM 'reserved'` |
| `M6` | `ChargesWithinHold` | `_preflight` without `credits.cover` |
| `M7` | `CreditsConserved` | `settle_reservation_with_slack` that gives back the whole hold |
| `M8` | `NoPendingOnClosedHold` | `settle_hold` that leaves held charges held |
| `M9` | `BounceRefundOnce` | `refund_bounced` without the `FOR UPDATE` re-check of `state = 'charged'` |
| `M10` | `TurnEndRefundsUnfinished` | `refund_unfinished` that refunds every held charge, finished row or not |
| `M11` | `CapRespected` | `reserve_project_credits` without the hard cap check |
| `M12` | `WalletNeverNegative` | `reserve_credits` without `credits_remaining >= p_amount` |
| `M13` | `ChargeOnLiveHold` | a stored-hold record without `Tab.lock` |

The `Tab.lock` check in `RunFinalizeHold` has no mutant. No invariant depends on it: a closed hold has no held charges, because `settle_hold` finalizes them, so `finalize_hold` on a closed hold changes nothing in the model.
