# Digital Wallet & P2P Payment System — Invariants

## Purpose

This document defines properties that must **always remain true**.

Requirements describe what the system should do.

Invariants describe what the system must **never violate**, particularly during failures, retries, concurrent requests, and unexpected input.

Every feature that affects financial state must identify which invariants it can affect and verify them through tests.

---

# INV-1 — Ledger Balance

For every completed financial transaction, the ledger must remain balanced.

Conceptually:

```text
Total Debits = Total Credits
```

A completed financial operation must not create or destroy value through an accounting imbalance.

---

# INV-2 — No Negative Available Balance

A wallet must not complete a transaction that causes its available balance to become negative.

For every completed operation:

```text
available_balance >= 0
```

This must remain true even when multiple operations are attempted concurrently.

---

# INV-3 — Idempotency

For an authenticated user and idempotency key, a financial operation must not be applied more than once.

Conceptually:

```text
(user, idempotency_key)
        ↓
at most one financial operation
```

A retry must resolve to the original operation/result rather than creating another financial effect.

---

# INV-4 — Atomic Financial Transfer

A transfer must be atomic.

Either:

```text
Debit sender
+
Credit recipient
+
Record corresponding transaction
```

succeeds as one operation,

or the financial operation has no partial effect.

The system must not leave:

```text
sender debited
recipient not credited
```

or another equivalent partial state.

---

# INV-5 — Refund Limit

The cumulative refunded amount for a transaction must never exceed the original transaction amount.

```text
total_refunded <= original_amount
```

This must hold across multiple partial refunds as well as full refunds.

---

# INV-6 — Valid Money Request State Machine

Money requests must follow the allowed state transitions.

Initial state:

```text
PENDING
```

Allowed terminal transitions:

```text
PENDING → APPROVED
PENDING → DECLINED
PENDING → EXPIRED
```

Terminal states cannot transition again.

Invalid examples:

```text
APPROVED → DECLINED
APPROVED → EXPIRED
DECLINED → APPROVED
EXPIRED → APPROVED
```

must not be allowed.

---

# INV-7 — Authorization Boundary

A user must not be able to access or modify another user's financial resources without authorization.

This applies to:

- wallets
- balances
- transactions
- money requests
- refunds
- other protected financial resources

Authorization must be enforced server-side.

---

# INV-8 — Auditability

Financially significant and administrative actions that require auditing must produce the corresponding audit record.

The system must not silently perform an auditable operation without recording it.

The set of actions that require auditing is defined by R9, which is currently skipped. While
it is skipped no action is in that set, so this invariant is held for want of anything to
apply to rather than by an audit trail being in place. It is **not** satisfied by the absence
of the requirement, and it becomes mandatory again in full the moment R9 is implemented.

Financially significant actions are separately and unconditionally recorded by R2, R4 and R5:
each writes a transfer row before any money moves, and that row carries the outcome. Those
records are the financial record itself rather than an audit trail, and they do not satisfy
this invariant.

---

# INV-9 — Wallet/Ledger Consistency

Where the system maintains a current wallet balance, that balance must remain consistent with the ledger according to the system's balance calculation rules.

Conceptually:

```text
wallet balance
=
balance derived from valid ledger activity
```

If reconciliation detects a difference, the system must report the inconsistency rather than silently treating the values as equivalent.

The comparison this invariant describes is in scope under R1 and is exercised today: a stored
balance is compared against the balance its ledger implies. R10, which would surface such a
difference to a user, is skipped.

---

# INV-10 — Transaction History Preservation

Completed financial history must not be destroyed or rewritten merely to represent a later correction.

For example, a refund should not modify the original transaction into a different historical transaction.

Instead:

```text
Original Transaction
        +
Compensating Refund Entry
```

must preserve the historical record.

---

# INV-11 — Retry Safety

Retrying a failed or already-completed operation must not create an unintended additional financial effect.

This is especially important for:

- network retries
- client retries
- batch retries

Retry behavior must be explicitly defined for each financial operation.

---

# INV-12 — State/Financial Consistency

A state transition that represents a financial operation must not claim success unless the corresponding financial operation has succeeded.

For example, approving a money request must not result in:

```text
Request = APPROVED
Transfer = FAILED
```

unless the system explicitly defines and safely handles such a state.

For the initial system design, approval should use the same atomic transfer mechanism so the financial operation and relevant state change remain consistent.

---

# INV-13 — Ownership

Resources owned by a user must remain associated with the correct owner.

For example:

```text
Wallet → User
Transaction → relevant financial parties
Money Request → requester / target participant
```

An operation must not accidentally associate a financial resource with the wrong user.

---

# INV-14 — Analytics Must Not Mutate Financial State

Analytics and reporting operations must be read-only with respect to financial state.

Running an analytics query must not:

- modify balances
- create transactions
- modify ledger entries
- alter transaction states

---

# INV-15 — Batch Processing Safety

Batch processing must be safe to retry.

Running a batch operation multiple times must not cause duplicate financial effects.

For example:

```text
Process a batch
        ↓
an item already processed
        ↓
no second financial operation for that item
```

An interrupted batch must be recoverable by running it again, and recovery must not become a
second way of moving money.

This is held by the key each item spends rather than by any lock. `batch:<batch_id>:<position>`
is derived, never accepted from a caller, so every attempt at one item spends the same key for
the same operation, and R3's partial unique index then allows exactly one of those attempts to
create a transfer however many arrive at once. Two consequences fall out of that and are both
tested:

- an item that has already been paid is answered from the transfer that paid it, so re-running
  a batch moves nothing;
- an item that _failed_ gave its key back when it failed, so the same key names a fresh attempt
  and the payment becomes payable once there is money to pay it with.

Recovery is a second run of the same code, not a second mechanism: a claim on a batch older than
the grace period is taken over by `reassert_claim`, and every item's key still decides what may
be spent. There is no worker, queue or scheduler involved.

---

# INV-16 — Terminal State Immutability

Once an operation reaches a terminal state, normal feature operations must not silently move it back into an earlier state.

Examples include:

```text
APPROVED
DECLINED
EXPIRED
```

for money requests.

Any future operation that intentionally introduces a new lifecycle must be represented as a new operation/state according to the requirements rather than silently rewriting history.

---

# INV-17 — Financial State Changes Are Transactional

A financial state change must use the database transaction/concurrency mechanisms required by the persistence layer to prevent partially committed financial state.

The implementation must account for:

- concurrent requests
- failed operations
- retries
- transaction rollback

The exact mechanism is an implementation decision, but the invariant is mandatory.

---

# INV-18 — Authorization Cannot Be Bypassed Through Alternate Endpoints

If a resource is protected, every API path capable of modifying or exposing that resource must enforce the appropriate authorization.

Security must not depend on one particular frontend screen or endpoint.

---

# Invariant Verification

Each feature must identify the invariants it can affect.

Example:

```text
Wallet
→ INV-1
→ INV-2
→ INV-9
→ INV-13
→ INV-17

Transfer
→ INV-1
→ INV-2
→ INV-3
→ INV-4
→ INV-7
→ INV-9
→ INV-11
→ INV-17

Money Request
→ INV-3
→ INV-4
→ INV-6
→ INV-7
→ INV-12
→ INV-16

Refund
→ INV-1
→ INV-4
→ INV-5
→ INV-10
→ INV-11
→ INV-17

Batch Processing
→ INV-1
→ INV-2
→ INV-3
→ INV-4
→ INV-7
→ INV-9
→ INV-11
→ INV-13
→ INV-15
→ INV-17

Analytics
→ INV-7
→ INV-13
→ INV-14
```

`INV-6` and `INV-16` govern the money request lifecycle and are satisfied by R4. They are not
part of batch processing now that this requirement is not responsible for expiring requests.

An implementation should not simply state that an invariant is satisfied.

Where practical, the invariant should be backed by an automated test.

---

# Completion Rule

A feature affecting financial state is not complete until:

```text
Requirements implemented
        +
Affected invariants identified
        +
Relevant failure cases considered
        +
Automated tests added
        +
Tests executed successfully
```

The goal is not merely to make the happy path work.

The system must remain correct under:

- retries
- concurrent operations
- invalid input
- authorization failures
- partial failures
- duplicate requests
- state-transition errors
- batch reprocessing
