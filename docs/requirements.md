# Digital Wallet & P2P Payment System — Requirements

## Purpose

This document defines **what the system must do**.

It is the functional source of truth for feature implementation.

Architecture and code organization are defined separately in:

```text
docs/reference-architecture.md
```

Correctness constraints are defined in:

```text
docs/invariants.md
```

A feature should not be considered complete unless its applicable requirements are implemented and verified.

---

# Delivery Scope

The committed feature set for this product is:

```text
R1  Wallet & Balance Management      implemented
R2  P2P Transfers                   implemented
R3  Idempotent Transfers            implemented
R4  Money Requests                  implemented
R5  Refunds & Reversals             implemented
R8  Authentication, RBAC            implemented
R7  Batch Processing                implemented
R11 Analytics                       last
```

That is eight end-to-end capabilities, inside the five-to-ten range the brief asks for. The
list is deliberately closed: a feature is added only by changing this section first, so the
documented scope and the shipped scope cannot drift apart.

## Not in scope

Three requirements are deliberately not being built.

| Requirement                 | Status  |
| --------------------------- | ------- |
| R6 — Rate & Velocity Checks | Skipped |
| R9 — Audit Logs             | Skipped |
| R10 — Reconciliation        | Skipped |

The reasoning is recorded here rather than left implicit, because a skipped requirement
normally looks like an oversight:

- **R6** defines a soft flag with nothing acting on it. It would add a risk-scoring surface
  whose output is advisory and inert, which is harder to judge correct than to explain.
  R11 answers the related need — an account understanding its own activity — with figures
  that mean something on their own.
- **R9** has nothing left to record. R8 deliberately ships no administrative operation, so
  there are no administrative actions to audit, and every financially significant action is
  already recorded by the transfer and the ledger entries it wrote. An audit trail built now
  would duplicate the financial record rather than extend it.
- **R10** compares a stored balance against the balance its ledger implies. That comparison
  already exists and already runs — `derived_balance` is asserted against
  `availableBalanceMinor` in the test suite and is re-checked on every seed. What R10 would
  add is a user-facing surface for reporting a discrepancy that is not currently occurring.

Their requirement text is retained below so any of them can be implemented later without
being rewritten, but nothing in the committed scope depends on them and no acceptance
criterion in scope expects them.

---

# R1 — Wallet & Balance Management

The system must allow users to hold and manage a digital wallet balance.

## Requirements

- Each user can have a wallet.
- A user's wallet balance must be accessible to the authenticated user.
- Users must be able to view their transaction history.
- Wallet ownership must be enforced.
- Wallet balance must be consistent with the transaction ledger.
- Financial history must preserve completed transactions rather than overwriting them.

## Acceptance Criteria

- Authenticated users can access their own wallet.
- A user cannot access another user's wallet.
- Wallet balance is returned correctly.
- Wallet transaction history can be retrieved.
- Ledger-derived balance can be compared with the stored/current balance where applicable.

---

# R2 — P2P Transfers

The system must support transfers between users.

## Requirements

A transfer must:

1. identify the sender
2. identify the recipient
3. specify an amount
4. validate that the sender has sufficient available balance
5. debit the sender
6. credit the recipient
7. record the transaction

The debit and credit must be treated as one atomic operation.

## Acceptance Criteria

- Valid transfers succeed.
- Invalid amounts are rejected.
- Insufficient balances are rejected.
- A sender cannot transfer money they do not have.
- Sender and recipient balances are updated correctly.
- The transaction is recorded.
- A failed transfer does not leave a partial balance update.
- Concurrent transfers cannot cause an invalid negative available balance.

---

# R3 — Idempotent Transfers

Transfer requests must support idempotency.

## Requirements

A client must be able to provide an idempotency key for a transfer.

For the same authenticated user and idempotency key:

- the original transfer must not be created again
- a retry must return the original result
- duplicate financial effects must not occur

## Acceptance Criteria

Given:

```text
User A
Idempotency Key: abc123
Amount: ₹500
```

If the request succeeds and the client retries with the same idempotency key, the system must not debit ₹500 again.

The retry must resolve to the original operation/result according to the API contract.

---

# R4 — Money Requests

Users must be able to request money from another user.

## Requirements

A money request must support:

```text
PENDING
   ↓
APPROVED
DECLINED
EXPIRED
```

Users must be able to:

- create a request
- view applicable requests
- approve a request
- decline a request

Expired requests must no longer be approvable.

Approval must use the same underlying transfer/payment mechanism as a normal transfer rather than implementing a separate financial path.

## Acceptance Criteria

- A request starts in `PENDING`.
- A pending request can be approved.
- A pending request can be declined.
- Expired requests cannot be approved.
- Terminal states cannot transition again.
- Approval creates the corresponding financial transfer atomically.
- Failed approval does not partially modify balances or request state.

---

# R5 — Refunds & Reversals

The system must support reversal of completed financial transactions.

## Requirements

Refunds/reversals must support:

- full refunds
- partial refunds

A refund must preserve the original transaction history.

Refunds must be represented through compensating financial entries rather than deleting or rewriting the original transaction.

## Acceptance Criteria

- A valid full refund succeeds.
- A valid partial refund succeeds.
- The original transaction remains available in history.
- Total refunded amount cannot exceed the original transaction amount.
- Refund operations are recorded.
- Financial balances remain consistent after refunds.

---

# R6 — Rate & Velocity Checks

> **Delivery status: skipped.** See [Delivery Scope](#delivery-scope). This requirement is
> retained, not withdrawn: it is unimplemented, nothing in the committed scope depends on it,
> and no acceptance criterion elsewhere expects it.

The system must identify potentially suspicious transaction activity using transaction velocity.

## Requirements

The system should support rolling-window checks based on:

- transaction count
- transaction amount

Activity exceeding configured thresholds should be flagged.

The initial behavior is a **soft flag**, not an automatic account block.

## Acceptance Criteria

- Transaction activity can be evaluated within a rolling window.
- Transaction count thresholds can be evaluated.
- Transaction amount thresholds can be evaluated.
- Suspicious activity can be flagged.
- A flag does not automatically modify the user's funds unless another explicit rule requires it.

---

# R7 — Batch Processing

The system must support sending several transfers as one recorded unit of work.

A single transfer is the right shape for one payment to one person. It is the wrong shape for
paying a list: twenty payments become twenty unrelated requests with twenty separate answers
and no single place to see which of them went through. A batch gives that list one identity,
one status, and one record of what each of its items did.

## Relationship to what was previously in this requirement

This requirement was previously "Deferred & Batch Processing" and also covered detecting and
lazily resolving expired money requests. That half is no longer part of this requirement,
and nothing is lost by dropping it: **R4 already resolves expiry**, lazily, on the path the
account takes. Listing requests expires the account's own overdue requests before the page is
read, and approving or declining one does the same before it acts, so nothing shown or acted
on is already overdue. `EXPIRED` remains a real terminal state under R4 and under INV-6.

Batch processing must be safe to run more than once.

## Requirements

A batch must:

- be created by an authenticated account and be owned by that account
- carry an ordered list of items, each naming a recipient, an amount, and optionally a note
- move money only through the transfer engine that already exists, never through a second
  money-moving path
- record the outcome of each item separately from the batch
- expose a batch-level summary derived from its items, not stored independently of them

A batch must have a state of its own, and an item must have a state of its own, and neither
may claim a financial outcome that did not happen.

Interrupted processing must be recoverable by running the batch again. Recovery must reuse
the transfer engine's existing in-flight recovery rather than introduce a second mechanism.

## Acceptance Criteria

- A batch can be created with one or more items, and each item is validated.
- Processing a batch produces one transfer per item, through the existing transfer engine.
- Each item records its own outcome, including the reason an item failed.
- The batch reports how many items completed, how many failed, and the totals for each.
- Running the same batch again does not produce a second transfer for an item that already
  succeeded.
- One item failing does not prevent the remaining items from being attempted.
- An item that fails leaves every balance and ledger entry exactly as it was.
- Only the account that owns a batch can read it or act on it; another account is refused
  without being told whether it exists.
- Processing a batch conserves the system total and violates no existing invariant.

## Scope constraints

Batch processing must not introduce a background worker, a task queue, a scheduler, or any
external service. It runs on the request path, as the transfer engine's own recovery sweep
already does.

Batch processing must not add a second money-moving implementation. `execute_transfer` is
the engine, and a batch item is a call to it.

## Decisions taken

These were open when this requirement was written. Each is settled below with the reason,
because every one of them changes either the domain model or the public API, and the reasoning
is what makes the next change to them a deliberate one.

### Validation timing, and why it is not per-item failure

A batch is validated **whole, at creation**, and every fault is reported at once against the
item it belongs to, under `items.<n>.<field>`. Per-item _failure_ is reserved for what happens
when the batch is **run**.

The acceptance criterion "one item failing does not prevent the remaining items from being
attempted" is about a payment that could not be made, not about a row that was typed wrongly.
An unpayable amount is discovered one row at a time, mid-run, and must not cost the other rows
their chance. A bad email address is known before anything is attempted, and refusing the whole
list at once is strictly better than writing twenty rows and then reporting that row 7 was
never payable — the person building the list can still correct it, because items are immutable.
Recording a creation-time fault as an item outcome would also make it permanent, since a failed
item is retryable only because its key was released, and a row that was never valid has no key
to release.

### Creation and processing are two deliberate actions

`POST /api/v1/batches` writes the list and **moves no money**. `POST
/api/v1/batches/<id>/processing` is what pays it. A list somebody is still typing is therefore
never a list of payments, a list that turns out to be wrong can be corrected before any of it
is spent, and a batch is retried by an explicit act rather than by arriving again.

### The batch state machine

```text
  claim, from PENDING | COMPLETED | PARTIALLY_COMPLETED | FAILED
      │
      ▼
  PROCESSING ──┬──► COMPLETED            every item paid
                ├──► PARTIALLY_COMPLETED  some failed, not all
                ├──► FAILED              every item failed
                └──► PENDING             refused part-way, nothing failed

  PROCESSING ──► claim older than 60s ──► PROCESSING   (taken over)
```

`PROCESSING` is not terminal and not exclusive: a claim older than
`PROCESSING_CLAIM_GRACE_SECONDS` (60s, bounded identically to the engine's
`RECOVERY_AGE_SECONDS`) may be taken over, because a run that claims a batch writes each item's
outcome as it goes, so a claim that old with items still pending belongs to a run that was
interrupted. `COMPLETED` is processable again on purpose — running it answers from the
transfers that paid it and moves nothing, which is the truthful answer to a question the UI has
no other way to answer. `PENDING` after a refusal is the state that means "not finished and
nothing has failed"; the summary is where the caller reads how much was paid before the refusal.

### The state is derived, never asserted

A batch's state is computed from its own items by a `$switch` over `$items` **in the same
update that records it**, in both places a state is written (`finalise` and `release`). That
means the pipeline stage has to evaluate the expression rather than store it, which is why both
are pipeline updates. The practical effect is that a batch cannot report `COMPLETED` while one of
its items says otherwise, and the summary is read off the same array rather than kept as a
second financial state.

### Items are embedded, not a second collection

`BatchItem` is a list field on the `Batch` document, following the `Wallet.pending_movements`
pattern that already exists in this codebase. A separate `BatchItem` collection would need a
sweep to reconcile an item's state against its batch's, and would make a disagreement between
the two _possible_. Embedding makes it structurally impossible, makes each item's outcome an
atomic single-document write, and bounds the document at `MAX_BATCH_ITEMS = 25` — so the 16MB
limit is not in play.

### Items are immutable

The list is fixed when the batch is created. An item's idempotency key is derived from its
position, so a list that could be edited after the fact would let a payment be re-addressed
under a key that was already spent on somebody else.

### Derived idempotency keys, and the one engine change they needed

Each item spends `batch:<batch_id>:<position>` — derived, never accepted from a caller, so
every attempt at one item spends the same key for the same operation. This is what makes an
item payable exactly once and what makes a failed item retryable: `mark_failed` releases the
key, so the same key names a fresh attempt rather than a spent one. It also adds a third
reserved prefix, so `RESERVED_KEY_PREFIXES` and `IDEMPOTENCY_KEY_RESERVED_MESSAGE` were
reworded to name `batch:`.

Two requests spending the same derived key at the same moment is the _normal_ case under batch
processing rather than a corner, and doing so exposed a real defect in the engine: a request
that lost the insert and then found the key already released was handed `None` to replay and
raised `AttributeError`, answering 500. `claim_or_replay` in `apps/transfers/services.py` now
races the key a bounded `MAX_KEY_CLAIM_ATTEMPTS` times and, if every refusal proved a
competitor that has since released, spends it once more without racing. No financial semantics
changed, and `EngineClaimTestCase` pins both answers.

### Who processes a batch

The owner, and nobody else. The previous version of this requirement included "an
administrative endpoint can trigger batch processing"; R8 ships no administrative operation, so
there is no such endpoint to build, and keeping the batch inside its owner's boundary is what
makes that unnecessary. Ownership is decided in the service layer from the authenticated
account, never from the request body.

---

# R8 — Authentication, RBAC & Authorization

The system must provide authenticated access and role-based authorization.

## Roles

The system supports:

```text
USER
MERCHANT
ADMIN
```

## Requirements

- Users must authenticate before accessing protected financial resources.
- Role-based permissions must be enforced server-side.
- Resource ownership must be enforced.
- Administrative operations must require appropriate authorization.
- Frontend restrictions must not be treated as the security boundary.

## Acceptance Criteria

- Unauthenticated users cannot access protected resources.
- Users cannot access another user's financial resources.
- Administrative operations reject unauthorized users.
- Role restrictions are enforced by the backend.

---

# R9 — Audit Logs

> **Delivery status: skipped.** See [Delivery Scope](#delivery-scope). This requirement is
> retained, not withdrawn. Note that R8 ships no administrative operation, so the set of
> administrative actions this requirement refers to does not currently exist, and every
> financially significant action is already recorded by the transfer and the ledger entries
> it wrote.

Important financial and administrative actions must produce audit records.

## Requirements

Audit records should capture sufficient information to determine:

- who performed the action
- what action occurred
- what resource/operation was affected
- when it occurred
- relevant outcome/context

Audit history must not be silently overwritten as part of normal application behavior.

## Acceptance Criteria

Financially significant actions create audit records where required.

Administrative actions create audit records where required.

Audit records can be used to understand what happened during an operation.

---

# R10 — Reconciliation

> **Delivery status: skipped.** See [Delivery Scope](#delivery-scope). This requirement is
> retained, not withdrawn. Note that the comparison it describes is already implemented and
> already exercised: `derived_balance` sums a wallet's ledger and is asserted against the
> stored `availableBalanceMinor` in the test suite, and is re-checked on every seed.

The system must provide reconciliation capabilities for detecting financial inconsistencies.

## Requirements

Reconciliation must be able to compare:

- ledger debit/credit activity
- wallet balances
- transaction state

The system must identify inconsistencies rather than silently ignoring them.

## Acceptance Criteria

- Ledger entries can be evaluated for balance.
- Wallet balances can be checked against ledger-derived balances.
- Inconsistencies can be detected.
- Reconciliation results identify affected resources/transactions where possible.

---

# R11 — Analytics

The system must give an account a view of its own activity that is derived from that
activity rather than a restatement of it.

## How this differs from History

History, required by R1, answers one question:

> What individual transactions happened?

It is a per-entry register. Each row is one ledger movement with its own moment, its own
counterparty, its own note, and the balance it produced. Activity lists money requests and
refunds in the same register style. Both are lists of things that happened, in order.

Analytics answers a different question:

> What can be learned from aggregating all of it?

Its unit is a period and a total, not an entry. It never shows one transaction.

The test for whether something belongs here is simple: **if it can be answered by scrolling
the history and adding up rows that are already visible, it is history, and it does not
belong in this requirement.** Wrapping the existing list in a chart is not analytics.

Analytics must be computed for the authenticated account. This is a per-account view of the
account's own money, not an operator's view of the system, so it does not extend to
administrative reporting.

## Requirements

Analytics must be read-only with respect to financial state.

Analytics must be computed by the server from stored data. It must not be assembled in the
client from a page of history the client already holds, because that would make the figures
a function of how much history happened to be loaded.

Analytics should support aggregate metrics derived from the stored ledger and transfers,
including:

- total sent and total received over a period
- net movement over a period
- count of transactions
- count and value of refunds, and refunds as a share of what was paid
- successful versus failed operations
- average transaction amount
- volume over time, grouped into buckets
- the counterparties most frequently transacted with

## Acceptance Criteria

- An authenticated account can read aggregate metrics for its own activity.
- Totals sent and received for a period equal the sum of the corresponding ledger entries
  for that period.
- A period containing no activity returns zero totals and an empty series, not an error.
- Counts distinguish completed operations from failed ones.
- Refund totals agree with the refunds recorded for the account in that period.
- Volume over time returns one bucket per period across the requested range, including the
  periods in which nothing happened.
- One account cannot read another account's analytics.
- Running analytics changes no balance, transfer, refund, request or ledger entry.
- Analytics does not require the whole history to be paged to the client.

## A distinction the implementation must not blur

Money totals and operation counts come from **different places**, and this is not a detail.

A ledger entry exists only for money that actually moved. A transfer row also exists for
operations that were attempted and failed. So:

- **totals** are the sum of ledger entries, and by construction contain no failed operation;
- **counts of successes and failures** come from transfer rows, and include the failures that
  no ledger entry describes.

Adding these two as though they share a denominator produces a figure that is wrong in a way
that is hard to notice. Any metric mixing the two must state which population it describes.

## Open design decisions

These are unresolved and must be settled before implementation.

- **Period selection.** Fixed presets such as the last 7, 30 or 90 days and all time, or a
  caller-supplied range?
- **Bucket size and boundary.** Day or week, and whether a bucket boundary is UTC or local.
  A period that crosses midnight is where this becomes visible.
- **Surface.** A page of its own, or a section within the existing Activity page. A separate
  page needs a place in the header navigation; a section does not.
- **Counterparties.** Whether "who I transact with most" belongs to this requirement or is a
  separate feature. It is the most expensive of the listed metrics, because it needs the
  counterparties resolved the way history already resolves them.
- **The opening entry.** A wallet's `OPENING` entry is a credit that no counterparty sent.
  It must not be counted as money received, while still being part of the balance. Whether
  it is shown as a separate line is a presentation choice.
- **Failed-operation visibility.** Whether a failure count is meaningful to the account at
  all, given that a failed transfer releases its idempotency key and leaves the balance
  untouched.

---

# R12 — Data Integrity

Financial records must remain internally consistent.

## Requirements

The system must preserve:

- atomic financial operations
- transaction history
- ledger consistency
- wallet balance consistency
- valid state transitions
- authorization boundaries
- idempotency guarantees

Financial operations must not rely on frontend behavior to maintain correctness.

---

# R13 — Testing & Quality

The project must have automated verification for important behavior.

## Requirements

Tests should cover:

- normal operations
- validation failures
- authorization failures
- financial failures
- state transitions
- duplicate requests
- idempotency
- concurrency-sensitive behavior
- ledger correctness
- rollback/atomicity
- refund limits
- reconciliation

The last of these is currently satisfied by the ledger-consistency assertions described in
[Delivery Scope](#delivery-scope): the stored balance is compared against the balance its
ledger implies. It would extend to a user-facing reconciliation surface only if R10 were
implemented.

Rate and velocity checks are absent from this list because R6 is skipped, not because they
were judged untestable.

The project should have:

- a deterministic test setup
- a working test command
- a deterministic seed/demo setup where applicable
- a successful application build

---

# R14 — Dependency & Starter Preservation

The project must preserve the established project setup unless a change is genuinely required.

## Requirements

Do not:

- unnecessarily upgrade dependencies
- replace existing libraries
- modify starter configuration for convenience
- remove existing project conventions
- introduce unnecessary dependencies

Any necessary dependency/configuration change must be explicitly justified.

---

# Requirement Traceability

Each implementation feature should identify the requirements it satisfies.

Example:

```text
Wallet Feature
→ R1

Transfer Feature
→ R2, R12

Idempotency Feature
→ R3, R12

Money Request Feature
→ R4, R12

Refund Feature
→ R5, R12

RBAC
→ R8

Batch Processing
→ R7, R12

Analytics
→ R11

Testing / Infrastructure
→ R13, R14
```

Skipped requirements, retained above but not satisfied by anything in the committed scope:

```text
Velocity Feature
→ R6        skipped

Audit
→ R9        skipped

Reconciliation
→ R10       skipped
```

Requirements may be satisfied incrementally across multiple features.

A requirement is complete only when its behavior has been implemented and verified.
