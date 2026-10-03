# Add Feature

## Purpose

Add exactly one feature to the application while preserving the existing architecture, conventions, dependencies, and behavior.

This skill defines **how a feature is added**.

It does not define what the feature should do. Feature behavior must come from:

- `docs/requirements.md`
- `docs/invariants.md`
- the existing application code
- the feature request provided by the user

The architectural source of truth is:

- `docs/reference-architecture.md`

Do not invent a different architecture when implementing a feature.

---

# 1. Read Before Changing Anything

Before writing code, read:

1. `docs/reference-architecture.md`
2. `docs/requirements.md`
3. `docs/invariants.md`
4. the existing code for the relevant feature
5. related shared code
6. existing tests, if present

Determine:

- where the feature belongs
- which existing features it interacts with
- which models/data structures are required
- which API endpoints are required
- which existing services/repositories can be reused
- which invariants the feature must preserve
- which requirements the feature satisfies

Do not start by creating `models.py`.

Start from the requirement and work toward the implementation.

---

# 2. Preserve the Existing Architecture

Follow the architecture documented in `docs/reference-architecture.md`.

## Backend

The backend uses modular application features.

A feature should follow the existing layered structure:

```text
backend/apps/<feature>/
├── models.py
├── repository.py
├── schema.py
├── services.py
├── views.py
└── urls.py
```

Not every file must be created if the feature genuinely does not require it.

Use the layers for their intended responsibilities:

```text
URL
 ↓
View
 ↓
Schema / Validation
 ↓
Service
 ↓
Repository
 ↓
Model / Database
```

### Views

Views should be thin.

They should:

- receive the request
- obtain authentication context
- validate input through schemas
- call the appropriate service
- return the response

Views must not contain:

- business rules
- complex database queries
- financial calculations
- authorization logic that belongs in services
- transaction workflows

### Schemas

Schemas handle request validation and normalization.

They should:

- validate input
- apply input constraints
- normalize values
- use the project's existing validation/error conventions

Do not put business workflows inside schemas.

### Services

Services contain business logic.

They should:

- enforce business rules
- enforce invariants
- perform authorization checks where appropriate
- coordinate repositories
- call other services when appropriate
- transform domain data
- raise the project's existing application errors

Services should remain stateless.

Do not introduce unnecessary service classes, managers, factories, or dependency-injection frameworks.

### Repositories

Repositories encapsulate database access.

They should:

- construct database queries
- perform persistence operations
- convert database-specific identifiers where required
- return the project's existing data representation

Do not place business rules inside repositories.

### Models

Models represent persisted domain data.

Before creating or changing a model, determine:

- entity ownership
- relationships
- required fields
- optional fields
- uniqueness constraints
- indexes
- state fields
- timestamps
- database field naming conventions

Use the database conventions already established by the project.

Do not introduce a new ORM, persistence abstraction, or database pattern.

---

# 3. Frontend Architecture

Follow the feature-first frontend structure documented in the reference architecture.

```text
src/
├── features/
│   └── <feature>/
│       ├── <FeatureComponent>.jsx
│       ├── <AnotherComponent>.jsx
│       └── <feature>.api.js
│
└── shared/
    ├── api/
    │   └── client.js
    ├── components/
    └── utils/
```

Feature-specific components belong inside their feature directory.

Feature-specific API calls belong in:

```text
<feature>.api.js
```

All API communication must use the existing shared API client.

Do not create another API client.

Do not introduce a global state library unless the existing project already uses one.

Prefer the project's existing React patterns:

- `useState`
- `useEffect`
- `useMemo`
- `useCallback`
- props
- callbacks

Shared components should remain generic.

Do not put feature-specific business logic into shared components.

---

# 4. Discover Before Designing

Before implementation, inspect the existing application.

Determine:

### Backend

- existing apps
- existing models
- existing authentication
- existing authorization
- existing error handling
- existing validation
- existing repositories
- existing service patterns
- existing API response format
- existing URL structure
- existing database configuration

### Frontend

- existing features
- existing components
- existing API modules
- shared API client
- shared UI components
- current application state flow
- current navigation/view mechanism

Reuse existing patterns whenever possible.

Do not create a new abstraction merely because another abstraction would be theoretically cleaner.

---

# 5. Domain Design Before Implementation

For every non-trivial feature, first determine the domain model.

Document or explain:

### Entities

What entities are required?

### Relationships

How do those entities relate?

Example:

```text
User
  │
  └── Wallet
        │
        └── Ledger Entries
```

### State

Does the feature contain a state machine?

If yes, explicitly define:

```text
CURRENT STATE
      ↓
ALLOWED TRANSITION
      ↓
NEW STATE
```

### Constraints

Identify:

- required fields
- unique fields
- ownership constraints
- indexes
- valid state transitions
- authorization boundaries

### Invariants

Identify which rules must always remain true.

For example:

```text
An operation must not leave the system in a partially completed state.
```

Do not implement a domain model until its purpose and constraints are understood.

---

# 6. API Design

Before implementation, identify the API required by the feature.

For each endpoint define:

```text
METHOD
PATH
AUTHENTICATION
INPUT
VALIDATION
SUCCESS RESPONSE
ERROR RESPONSES
AUTHORIZATION
```

Follow the existing API naming and response conventions.

Use plural resource names where that matches the existing backend convention.

Do not create endpoints that are not required by the feature.

Do not expose database models directly if the existing application uses service/response transformations.

---

# 7. Implementation Plan

Before changing code, produce a short implementation plan containing:

```text
Feature:
Purpose:

Requirements satisfied:
- R...

Invariants affected:
- INV...

Backend files:
- create:
- modify:

Frontend files:
- create:
- modify:

Database changes:
- ...

API changes:
- ...

Business rules:
- ...

Tests:
- ...
```

If the task is ambiguous or the implementation would require architectural changes, stop and ask for clarification rather than guessing.

---

# 8. Implement the Smallest Correct Change

Implement only the requested feature.

Do not:

- refactor unrelated code
- rename unrelated files
- reorganize the entire project
- upgrade dependencies
- replace libraries
- change starter configuration
- change formatting configuration unnecessarily
- introduce unnecessary abstractions
- introduce new state-management libraries
- introduce new architectural layers
- modify unrelated UI
- rewrite working code for stylistic reasons

If an existing component/service/repository can be reused, prefer reuse.

If an architectural change is genuinely necessary, explain why before making it.

---

# 9. Testing

Every feature must have appropriate automated tests.

Tests should cover the feature's behavior rather than only implementation details.

At minimum, consider:

### Happy path

Does the feature work correctly under valid input?

### Validation

What happens with invalid input?

### Authorization

Can an unauthorized user access or modify the resource?

### Failure

What happens when a required operation fails?

### Invariants

Does the feature preserve all affected invariants?

For stateful features:

- valid transitions
- invalid transitions
- terminal states

For financial features:

- atomicity
- duplicate requests
- concurrent operations
- rollback/failure behavior
- balance correctness
- ledger correctness
- idempotency where applicable

Use the project's existing test infrastructure.

Do not weaken or bypass tests merely to make implementation pass.

---

# 10. Adversarial Review

After implementation, review the feature as if trying to break it.

Ask:

### Concurrency

What happens if two requests happen simultaneously?

### Retries

What happens if the client retries the same request?

### Partial failure

What happens if one operation succeeds and another fails?

### Authorization

Can a user access another user's resource?

### Invalid state

Can an entity enter an impossible state?

### Duplicate operations

Can the same operation be performed twice unintentionally?

### Database consistency

Can persisted data violate an invariant?

### API misuse

What happens when malformed or unexpected input reaches the endpoint?

### Error handling

Does failure leave the system in a consistent state?

Fix issues found by this review if they are within the feature scope.

Do not expand the task into unrelated refactoring.

---

# 11. Verification

Before declaring the feature complete, verify:

- automated tests pass
- relevant existing tests still pass
- formatting/checks pass
- frontend build succeeds when applicable
- backend starts successfully
- API endpoints behave as expected
- database changes work
- authentication/authorization works
- affected invariants hold

If something cannot be verified, explicitly state that.

Never claim a test was run when it was not run.

Never claim an invariant was verified when it was only reasoned about.

---

# 12. Final Feature Report

After implementation, report:

```text
## Feature

<feature name>

## Requirements Completed

- R...
- R...

## Invariants Verified

- INV...
- INV...

## Files Created

- ...

## Files Modified

- ...

## API Changes

- ...

## Database Changes

- ...

## Tests Added

- ...

## Tests Run

- ...

## Verification

- ...

## Remaining Concerns

- ...
```

Keep the report factual.

---

# 13. Architecture Rules

The following rules are mandatory unless the user explicitly approves an architectural change.

### Backend

```text
URLs → Views → Schemas → Services → Repositories → Models
```

Keep responsibilities separated.

### Frontend

```text
Feature Components → Feature API → Shared API Client
```

Keep shared components generic.

### Cross-feature communication

Prefer:

- props
- callbacks
- shared utilities
- existing project mechanisms

Do not directly import another feature's internal implementation merely for convenience.

### Errors

Use the existing application error and response conventions.

Do not introduce a second error format.

### Authentication

Use the existing authentication mechanism.

Do not create another authentication system for one feature.

### Authorization

Use the existing authorization patterns.

Do not rely solely on frontend restrictions.

### Database

Use the existing database and persistence technology.

Do not introduce another persistence layer.

---

# 14. Dependency and Starter Protection

Do not modify dependency versions unless the feature genuinely cannot be implemented otherwise.

Do not perform dependency upgrades as part of feature implementation.

Do not modify starter/contract files unnecessarily.

Before modifying configuration, determine whether the change is:

1. required for the feature, or
2. merely a preference.

Only perform required changes.

If a required change affects the project's original setup, report it explicitly.

---

# 15. Scope Control

One invocation of this skill should implement **one feature**.

Do not combine multiple major features because they are related.

For example, do not implement:

```text
Wallet
+
Transfers
+
Refunds
+
Admin
```

in one task.

Instead:

```text
Wallet
↓
Transfer Engine
↓
Idempotency
↓
Concurrency
↓
Refunds
↓
...
```

Each feature should leave the repository in a working state.

---

# 16. Decision Rule

When there are multiple technically valid approaches:

1. Prefer the approach already used by the reference architecture.
2. Prefer an existing project pattern over a new abstraction.
3. Prefer the smallest implementation that satisfies the requirement.
4. Prefer explicit code over unnecessary abstraction.
5. Prefer preserving existing behavior over refactoring.
6. Prefer correctness and invariant preservation over convenience.

If the choice materially affects the domain model, data consistency, concurrency, or public API, explain the tradeoff before implementation.

---

# 17. Completion Criteria

A feature is complete only when:

```text
Requirement understood
        ↓
Domain model understood
        ↓
Invariants identified
        ↓
API defined
        ↓
Implementation follows architecture
        ↓
Tests added
        ↓
Adversarial review completed
        ↓
Tests/checks/build verified
        ↓
Requirements updated
        ↓
Feature reported
```

Do not mark a feature complete merely because the code compiles.

The feature must satisfy its requirements while preserving the application's architectural conventions and invariants.
