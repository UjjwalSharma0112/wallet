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

# R7 — Deferred & Batch Processing

The system must support deferred processing for operations that require later resolution.

## Requirements

The system must support:

- detection of expired/stale requests
- lazy resolution of expired requests
- administrative batch processing

Batch processing must be safe to retry.

## Acceptance Criteria

- Expired requests can be identified.
- Expired requests eventually transition to the appropriate terminal state.
- Processing the same expired request repeatedly does not produce duplicate financial effects.
- An administrative endpoint can trigger batch processing.
- Batch processing does not violate existing invariants.

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

The system must provide basic transaction analytics.

## Requirements

Analytics should support metrics including:

- transaction volume
- transaction count
- failure rate
- flagged/suspicious activity

Analytics must not modify financial records.

## Acceptance Criteria

- Transaction volume can be calculated.
- Transaction failures can be measured.
- Flagged activity can be reported.
- Analytics queries do not alter financial state.

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

Velocity Feature
→ R6

Deferred Processing
→ R7

RBAC
→ R8

Audit
→ R9

Reconciliation
→ R10

Analytics
→ R11

Testing / Infrastructure
→ R13, R14
```

Requirements may be satisfied incrementally across multiple features.

A requirement is complete only when its behavior has been implemented and verified.
