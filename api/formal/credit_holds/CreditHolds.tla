----------------------------- MODULE CreditHolds -----------------------------
(***************************************************************************)
(* The credit-hold lifecycle (#660): one organization wallet, one project  *)
(* with a hard monthly cap, a chat turn's hold and a stored-hold run's      *)
(* hold, the per-row charges each hold pays for, the reaper, campaign       *)
(* enrolment, hand edits, top-ups, deductions and hard-bounce refunds.      *)
(*                                                                         *)
(* One action is one committed transaction. Where a transaction reads a    *)
(* row it does not lock (refund_bounced reads the hold's status from its   *)
(* snapshot), the read and the write are two actions. The coverage record  *)
(* and the assumptions are in README.md, next to this file.               *)
(*                                                                         *)
(* A line tagged [Mn] is the line a mutant of check.sh changes.            *)
(***************************************************************************)
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS
    Rows,            \* list rows a charge can bill
    InitWallet,      \* user_credits.credits_remaining at the start
    Cap,             \* projects.credit_cap, credit_cap_mode = 'hard'
    MaxDeposits,     \* top-ups, purchases, renewals, admin grants (increment_credits)
    MaxWithdrawals,  \* admin deductions, plan expiry (deduct_credits, pay_plan_expiry_owed)
    MaxCharges,      \* bound on row_charges records, to keep the state space finite
    MaxQuote,        \* the most credits one paid call quotes
    MaxSends         \* campaign sends that can bounce, over all rows

Holds == {"turn", "run"}   \* TurnCredits' hold (reference_type 'job'), a Tab.stored hold (run_credits, automations)

VARIABLES
    wallet,      \* user_credits.credits_remaining
    deposited,   \* credits added outside the hold lifecycle
    withdrawn,   \* credits deducted outside the hold lifecycle
    bounceBack,  \* credits given back by bounce_refund.refund (reference_type 'email_bounce')
    status,      \* credit_transactions.status of each hold: none (not taken), reserved, committed, released
    amount,      \* credit_transactions.amount of each hold
    kept,        \* settlement_net_credits of each closed hold: 0 when released
    charges,     \* row_charges: <<[hold, row, credits, state, why, refundOpen, endRow, live]>>
    finished,    \* a row holds every required column (rows.UNFINISHED_SQL is false)
    turn,        \* TurnCredits: pc, known (tab.hold.amount), spent, calls (quotes covered and not yet delivered)
    run,         \* a stored-hold run: pc, quote, spent
    bounce       \* per row: sends that bounced, the picks of bounces not yet applied, and lost (a bounce refunded nothing)

vars == <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, charges, finished, turn, run, bounce>>

Min(a, b) == IF a < b THEN a ELSE b
Max(a, b) == IF a > b THEN a ELSE b

RECURSIVE SeqSum(_)
SeqSum(s) == IF s = <<>> THEN 0 ELSE Head(s) + SeqSum(Tail(s))

RECURSIVE TotalIn(_, _)
TotalIn(cs, S) == IF S = {} THEN 0
                  ELSE LET i == CHOOSE x \in S : TRUE IN cs[i].credits + TotalIn(cs, S \ {i})

RemoveAt(s, k) == [i \in 1..(Len(s) - 1) |-> IF i < k THEN s[i] ELSE s[i + 1]]

SetToSeq(S) == CHOOSE f \in [1..Cardinality(S) -> S] : \A i, j \in 1..Cardinality(S) : i # j => f[i] # f[j]

Reserved(h) == status[h] = "reserved"

Of(cs, h) == {i \in DOMAIN cs : cs[i].hold = h}

\* settle_hold: sum(credits) FROM row_charges WHERE hold_id = h AND state <> 'refunded'
Unrefunded(cs, h) == {i \in Of(cs, h) : cs[i].state # "refunded"}   \* [M4]

\* What a hold must keep: every charge not refunded while the hold was still reserved. A refund after the close
\* (a bounce) is given back through the wallet, not through the hold.
Billed(cs, h) == {i \in Of(cs, h) : ~(cs[i].why # "" /\ cs[i].refundOpen)}

\* project_month_spend (20261021095255): a reserved hold counts whole, a settled one what it kept, a released one
\* nothing; an email_bounce refund counts back. Never below 0. One month: the model has no month boundary.
HoldSpend(h) == CASE status[h] = "reserved" -> amount[h]
                  [] status[h] = "committed" -> kept[h]
                  [] OTHER -> 0
ProjectSpend == Max(0, HoldSpend("turn") + HoldSpend("run") - bounceBack)

\* row_charges.charge: one record per landed row, 'held' while the row misses a required column, else 'charged'.
NewCharges(h, S, fin) ==
    LET rs == SetToSeq(S)
    IN [k \in 1..Len(rs) |-> [hold |-> h, row |-> rs[k], credits |-> 1,
                              state |-> IF fin[rs[k]] THEN "charged" ELSE "held",
                              why |-> "", refundOpen |-> FALSE, endRow |-> "", live |-> Reserved(h)]]

\* settle_hold, row_charges.finalize_hold: every held charge of h becomes final.
FinalizeOf(cs, h) == [i \in DOMAIN cs |-> IF cs[i].hold = h /\ cs[i].state = "held"
                                          THEN [cs[i] EXCEPT !.state = "charged"] ELSE cs[i]]

\* row_charges.refund_unfinished: a held charge on a row still unfinished is refunded, every other one charged.
\* endRow records whether the row was finished then, for TurnEndRefundsUnfinished.
RefundUnfinished(cs, h) ==
    [i \in DOMAIN cs |-> IF cs[i].hold = h /\ cs[i].state = "held"
                         THEN LET end == IF finished[cs[i].row] THEN "finished" ELSE "unfinished"
                              IN IF finished[cs[i].row]   \* [M10]
                                 THEN [cs[i] EXCEPT !.state = "charged", !.endRow = end]
                                 ELSE [cs[i] EXCEPT !.state = "refunded", !.why = "unfinished", !.refundOpen = TRUE,
                                                    !.endRow = end]
                         ELSE cs[i]]

\* settle_hold (20261013183923) on the charges cs, under the hold's row lock: keeps LEAST(amount, unrefunded
\* credits) through settle_reservation_with_slack, or releases the hold whole when that is 0.
Close(cs, h) ==
    LET spent == Min(amount[h], TotalIn(cs, Unrefunded(cs, h)))
    IN /\ charges' = FinalizeOf(cs, h)   \* [M8]
       /\ status' = [status EXCEPT ![h] = IF spent > 0 THEN "committed" ELSE "released"]
       /\ kept' = [kept EXCEPT ![h] = spent]
       /\ wallet' = wallet + amount[h] - spent   \* [M7]

\* reserve_project_credits (20261006134459) under the project row lock, then reserve_credits (20261006110243).
CanReserve(a) == /\ ProjectSpend + a <= Cap   \* [M11]
                 /\ wallet >= a              \* [M12]
TakeHold(h, a) == /\ status' = [status EXCEPT ![h] = "reserved"]
                  /\ amount' = [amount EXCEPT ![h] = a]
                  /\ wallet' = wallet - a

\* grow_reservation (20261013183818): the hold row FOR UPDATE, the cap under the project lock, the wallet.
GrowOK(h, g) == /\ Reserved(h)
                /\ ProjectSpend + g <= Cap   \* [M2]
                /\ wallet >= g               \* [M1]

Init ==
    /\ wallet = InitWallet
    /\ deposited = 0
    /\ withdrawn = 0
    /\ bounceBack = 0
    /\ status = [h \in Holds |-> "none"]
    /\ amount = [h \in Holds |-> 0]
    /\ kept = [h \in Holds |-> 0]
    /\ charges = <<>>
    /\ finished \in [Rows -> BOOLEAN]
    /\ turn = [pc |-> "running", known |-> 0, spent |-> 0, calls |-> <<>>]
    /\ run = [pc |-> "idle", quote |-> 0, spent |-> 0]
    /\ bounce = [r \in Rows |-> [sends |-> 0, pending |-> <<>>, lost |-> FALSE]]

(***************************************************************************)
(* The chat turn: app/agent/turn_credits.py, app/agent/loop.py,            *)
(* app/lists/delivery.py, app/billing/tab.py.                              *)
(***************************************************************************)

\* TurnCredits._hold with no tab: Tab.open -> credits.reserve. A refusal changes nothing.
TurnOpen(a) ==
    /\ turn.pc = "running"
    /\ status["turn"] = "none"
    /\ CanReserve(a)
    /\ TakeHold("turn", a)
    /\ turn' = [turn EXCEPT !.known = a]
    /\ UNCHANGED <<deposited, withdrawn, bounceBack, kept, charges, finished, run, bounce>>

\* TurnCredits._hold with a tab: Tab.cover -> credits.grow -> grow_reservation. NULL (hold gone, wallet or cap
\* short) changes nothing. The model lets the turn grow at any time, not only when a call is short.
TurnGrow(g) ==
    /\ turn.pc = "running"
    /\ GrowOK("turn", g)
    /\ amount' = [amount EXCEPT !["turn"] = @ + g]
    /\ wallet' = wallet - g
    /\ turn' = [turn EXCEPT !.known = amount["turn"] + g]
    /\ UNCHANGED <<deposited, withdrawn, bounceBack, status, kept, charges, finished, run, bounce>>

\* AgentLoop._preflight: a paid call runs only once credits.cover(planned_cost + quote) passes, where unspent is
\* tab.hold.amount - spent. Parallel searches cover each call in turn before any handler starts. The check bounds
\* them to two.
TurnPreflight(q) ==
    /\ turn.pc = "running"
    /\ status["turn"] # "none"
    /\ Len(turn.calls) < 2
    /\ SeqSum(turn.calls) + q <= turn.known - turn.spent   \* [M6]
    /\ turn' = [turn EXCEPT !.calls = Append(@, q)]
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, charges, finished, run, bounce>>

\* AgentLoop._deliver in one transaction: TurnCredits.lock locks the hold first for every priced call
\* (Tab.lock -> credits.live, FOR UPDATE), then delivery.deliver stores the found rows up to the quote (some may
\* now hold every required column: fin) and Tab.record writes one charge per billed row. HoldGone rolls the whole
\* delivery back, and _finish_tool reports the call as crashed.
TurnDeliver(k, S, fin) ==
    /\ turn.pc = "running"
    /\ k \in 1..Len(turn.calls)
    /\ Cardinality(S) <= turn.calls[k]
    /\ Len(charges) + Cardinality(S) <= MaxCharges
    /\ LET rest == [turn EXCEPT !.calls = RemoveAt(@, k)]
       IN IF ~Reserved("turn")   \* [M3]
          THEN /\ turn' = rest
               /\ UNCHANGED <<charges, finished>>
          ELSE /\ finished' = [r \in Rows |-> IF r \in S THEN fin[r] ELSE finished[r]]
               /\ charges' = charges \o NewCharges("turn", S, fin)
               /\ turn' = [rest EXCEPT !.spent = @ + Cardinality(S)]
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, run, bounce>>

\* TurnCredits.finish in one transaction: Tab.refund_unfinished (locks the hold, HoldGone when the reaper closed
\* it), then Tab.close -> settle_hold. After HoldGone the turn only reads what the reaper kept.
TurnFinish ==
    /\ turn.pc = "running"
    /\ turn.calls = <<>>
    /\ turn' = [turn EXCEPT !.pc = "done"]
    /\ IF Reserved("turn")
       THEN Close(RefundUnfinished(charges, "turn"), "turn")
       ELSE UNCHANGED <<charges, status, kept, wallet>>
    /\ UNCHANGED <<deposited, withdrawn, bounceBack, amount, finished, run, bounce>>

\* A kill (hard time limit, deploy, OOM): the turn never closes its hold; in-flight work rolls back.
TurnDie ==
    /\ turn.pc = "running"
    /\ turn' = [turn EXCEPT !.pc = "dead", !.calls = <<>>]
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, charges, finished, run, bounce>>

(***************************************************************************)
(* A stored-hold run: app/scheduling/run_credits.py, app/automations/,     *)
(* app/watchlists/runner.py. Its row keeps the hold id between steps.      *)
(***************************************************************************)

\* run_credits.hold: Tab.open holding the run's quote, need = want.
RunOpen(q) ==
    /\ run.pc = "idle"
    /\ CanReserve(q)
    /\ TakeHold("run", q)
    /\ run' = [run EXCEPT !.pc = "held", !.quote = q]
    /\ UNCHANGED <<deposited, withdrawn, bounceBack, kept, charges, finished, turn, bounce>>

\* Tab.lock, then Tab.record of the found rows up to the quote. HoldGone rolls the transaction back and stops the run.
\* closeNow: run_credits.deliver, watchlists/runner.py and lists/row_phone.py also Tab.close in the same
\* transaction. Otherwise (an automation step in automations.run.guarded) the hold stays reserved for later steps.
RunRecord(S, fin, closeNow) ==
    /\ run.pc = "held"
    /\ closeNow \/ S # {}
    /\ run.spent + Cardinality(S) <= run.quote
    /\ Len(charges) + Cardinality(S) <= MaxCharges
    /\ IF ~Reserved("run")   \* [M13]
       THEN /\ run' = [run EXCEPT !.pc = "done"]
            /\ UNCHANGED <<charges, finished, status, kept, wallet>>
       ELSE LET cs == charges \o NewCharges("run", S, fin)
            IN /\ finished' = [r \in Rows |-> IF r \in S THEN fin[r] ELSE finished[r]]
               /\ run' = [run EXCEPT !.spent = @ + Cardinality(S), !.pc = IF closeNow THEN "done" ELSE "held"]
               /\ IF closeNow
                  THEN Close(cs, "run")
                  ELSE /\ charges' = cs
                       /\ UNCHANGED <<status, kept, wallet>>
    /\ UNCHANGED <<deposited, withdrawn, bounceBack, amount, turn, bounce>>

\* automations/instantly_step.py: row_charges.finalize_hold inside guarded, with the hold locked.
RunFinalizeHold ==
    /\ run.pc = "held"
    /\ IF Reserved("run")
       THEN /\ charges' = FinalizeOf(charges, "run")
            /\ UNCHANGED run
       ELSE /\ run' = [run EXCEPT !.pc = "done"]
            /\ UNCHANGED charges
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, finished, turn, bounce>>

\* tab.close_stored -> settle_hold, HoldGone suppressed: an automation's last step (automations/runner.py), a
\* stopped run (automations/service.py, watchlists/service.py).
RunClose ==
    /\ run.pc = "held"
    /\ run' = [run EXCEPT !.pc = "done"]
    /\ IF Reserved("run")
       THEN Close(charges, "run")
       ELSE UNCHANGED <<charges, status, kept, wallet>>
    /\ UNCHANGED <<deposited, withdrawn, bounceBack, amount, finished, turn, bounce>>

RunDie ==
    /\ run.pc = "held"
    /\ run' = [run EXCEPT !.pc = "dead"]
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, charges, finished, turn, bounce>>

(***************************************************************************)
(* Everyone else.                                                          *)
(***************************************************************************)

\* worker.reap_stale_reservations -> reap_stale_reservations_ids -> settle_hold. The model ignores the 20-minute
\* threshold: the reaper may close a reserved hold at any time, also under a live holder.
Reap(h) ==
    /\ Reserved(h)
    /\ Close(charges, h)
    /\ UNCHANGED <<deposited, withdrawn, bounceBack, amount, finished, turn, run, bounce>>

\* row_charges.finalize (campaign enrolment from a list, a draft, a watchlist or an automation): every held
\* charge on the row becomes final, whatever its hold's status.
Enroll(r) ==
    /\ \E i \in DOMAIN charges : charges[i].row = r /\ charges[i].state = "held"
    /\ charges' = [i \in DOMAIN charges |-> IF charges[i].row = r /\ charges[i].state = "held"
                                            THEN [charges[i] EXCEPT !.state = "charged"] ELSE charges[i]]
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, finished, turn, run, bounce>>

\* A hand edit, or another call, fills or clears a required column.
EditRow(r) ==
    /\ finished' = [finished EXCEPT ![r] = ~@]
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, charges, turn, run, bounce>>

\* increment_credits: Stripe checkout, renewal, auto top-up, admin grant.
Deposit ==
    /\ deposited < MaxDeposits
    /\ wallet' = wallet + 1
    /\ deposited' = deposited + 1
    /\ UNCHANGED <<withdrawn, bounceBack, status, amount, kept, charges, finished, turn, run, bounce>>

\* deduct_credits (admin, plan_period expiry) refuses more than the wallet holds; pay_plan_expiry_owed takes at
\* most the refund that fired it.
Withdraw ==
    /\ withdrawn < MaxWithdrawals
    /\ wallet >= 1
    /\ wallet' = wallet - 1
    /\ withdrawn' = withdrawn + 1
    /\ UNCHANGED <<deposited, bounceBack, status, amount, kept, charges, finished, turn, run, bounce>>

\* A campaign can send the address only once a charge that bought it is final ('charged'): a held one is hidden.
Sendable(r) == \E i \in DOMAIN charges : charges[i].row = r /\ charges[i].state = "charged" /\ charges[i].credits > 0

\* row_charges.refund_bounced's subquery, from the statement's snapshot: h.status is joined, not locked.
BounceCandidates(r) ==
    {i \in DOMAIN charges : /\ charges[i].row = r
                            /\ charges[i].state = "charged"
                            /\ charges[i].credits > 0
                            /\ ~Reserved(charges[i].hold)}   \* [M5]

Newest(S) == IF S = {} THEN 0 ELSE CHOOSE i \in S : \A j \in S : j <= i

\* outreach/bounces.record -> bounce_refund.refund, step 1: a send of the row's address bounced hard, and the
\* statement's snapshot picks the newest final charge that bought the address, on a hold that is not reserved.
\* bounces.record marks the send bounced hard first (_MARK) and ignores a later report of it, so each send's refund
\* runs once. Two sends (two campaigns) can bounce at the same time.
Sends == LET RECURSIVE S(_) S(rs) == IF rs = {} THEN 0 ELSE LET r == CHOOSE x \in rs : TRUE IN bounce[r].sends + S(rs \ {r})
         IN S(Rows)

BounceRead(r) ==
    /\ Sends < MaxSends
    /\ Sendable(r)
    /\ bounce' = [bounce EXCEPT ![r] = [@ EXCEPT !.sends = @ + 1, !.pending = Append(@, Newest(BounceCandidates(r)))]]
    /\ UNCHANGED <<wallet, deposited, withdrawn, bounceBack, status, amount, kept, charges, finished, turn, run>>

\* Step 2, same transaction: FOR UPDATE OF c, r waits for a concurrent refund and re-checks the charge's state on its
\* newest version, then the charge becomes refunded, take_back clears the row's cells and increment_credits gives
\* the credit back. A bounce that refunds nothing while the row still has a final, unrefunded charge is lost.
BounceApply(r, k) ==
    /\ k \in 1..Len(bounce[r].pending)
    /\ LET i == bounce[r].pending[k]
           rest == [bounce[r] EXCEPT !.pending = RemoveAt(@, k)]
       IN IF i # 0 /\ charges[i].state = "charged"   \* [M9]
          THEN /\ charges' = [charges EXCEPT ![i] = [@ EXCEPT !.state = "refunded", !.why = "bounce",
                                                         !.refundOpen = Reserved(charges[i].hold)]]
               /\ wallet' = wallet + charges[i].credits
               /\ bounceBack' = bounceBack + charges[i].credits
               /\ finished' = [finished EXCEPT ![r] = FALSE]
               /\ bounce' = [bounce EXCEPT ![r] = rest]
          ELSE /\ bounce' = [bounce EXCEPT ![r] = [rest EXCEPT !.lost = @ \/ Sendable(r)]]
               /\ UNCHANGED <<charges, wallet, bounceBack, finished>>
    /\ UNCHANGED <<deposited, withdrawn, status, amount, kept, turn, run>>

Next ==
    \/ \E a \in 1..Cap : TurnOpen(a)
    \/ \E g \in 1..MaxQuote : TurnGrow(g)
    \/ \E q \in 1..MaxQuote : TurnPreflight(q)
    \/ \E k \in 1..2, S \in SUBSET Rows : \E fin \in [S -> BOOLEAN] : TurnDeliver(k, S, fin)
    \/ TurnFinish
    \/ TurnDie
    \/ \E q \in 1..MaxQuote : RunOpen(q)
    \/ \E S \in SUBSET Rows, closeNow \in BOOLEAN : \E fin \in [S -> BOOLEAN] : RunRecord(S, fin, closeNow)
    \/ RunFinalizeHold
    \/ RunClose
    \/ RunDie
    \/ \E h \in Holds : Reap(h)
    \/ \E r \in Rows : Enroll(r) \/ EditRow(r) \/ BounceRead(r) \/ \E k \in 1..MaxSends : BounceApply(r, k)
    \/ Deposit
    \/ Withdraw

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Properties.                                                             *)
(***************************************************************************)

TypeOK ==
    /\ wallet \in Int
    /\ status \in [Holds -> {"none", "reserved", "committed", "released"}]
    /\ \A i \in DOMAIN charges : charges[i].state \in {"held", "charged", "refunded"}

\* The wallet never goes below zero.
WalletNeverNegative == wallet >= 0

\* Every credit the wallet gains or loses is booked: what the wallet holds, plus what reserved holds hold, plus what
\* closed holds kept, equals what came in minus what went out, bounce refunds included.
CreditsConserved ==
    wallet + HoldSpend("turn") + HoldSpend("run") = InitWallet + deposited - withdrawn + bounceBack

\* The project's hard monthly cap is never passed.
CapRespected == ProjectSpend <= Cap

\* A holder never bills more than its hold: settle_hold's LEAST never cuts a charge the user can read for free.
ChargesWithinHold == \A h \in Holds : TotalIn(charges, Billed(charges, h)) <= amount[h]

\* A closed hold kept exactly what its charges cost, less the charges refunded while it was reserved: no unpaid
\* cell, no charge paid twice, no refund that the hold also kept.
KeptIsWhatCharged ==
    \A h \in Holds : status[h] \in {"committed", "released"} => kept[h] = TotalIn(charges, Billed(charges, h))

\* A bounce gives its credit back once: through increment_credits after the close (bounceBack), or through the
\* close itself when the hold was still reserved. Never both.
BounceRefundOnce ==
    bounceBack = TotalIn(charges, {i \in DOMAIN charges : charges[i].why = "bounce" /\ ~charges[i].refundOpen})

\* At a live turn's end, a held charge is refunded exactly when its row is still unfinished (#45, #416).
TurnEndRefundsUnfinished ==
    \A i \in DOMAIN charges : charges[i].endRow = "unfinished" <=> charges[i].why = "unfinished"

\* Every charge was written while its hold was reserved.
ChargeOnLiveHold == \A i \in DOMAIN charges : charges[i].live

\* A held charge exists only on a reserved hold: no pending cell stays hidden for good.
NoPendingOnClosedHold == \A i \in DOMAIN charges : charges[i].state = "held" => Reserved(charges[i].hold)

\* Open finding (check.sh findings): «you pay only for what arrives» (#747). A hard bounce of an address bought by a
\* final charge gives that credit back. The model treats every bounce as one that qualifies: the verifier's re-check
\* calls the address invalid (bounces.record, reverified_invalid) and it came within REFUND_DAYS.
BounceRefundHonored == \A r \in Rows : ~bounce[r].lost

=============================================================================
