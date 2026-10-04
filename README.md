# Digital Wallet & P2P Payment System

A peer-to-peer payments application where people hold a balance, send money to each other,
request money, reverse payments that went wrong, pay several people at once, and see what they
actually spent over a period of time.

## The problem it solves

Splitting money between people is normally a chain of small failures. Someone pays up front and
has to chase the refund. Someone else is asked to pay and has no way to decline. A group dinner
becomes six separate transfers and six chances to get a name wrong. And once the money has
moved, nobody can answer a simple question: *where did it all go?*

This application treats a payment as a real financial operation rather than a database write.
Every movement is recorded in an append-only ledger, balances are derived from it and verified
against it, a payment that is retried cannot move money twice, and a wrong payment is reversed
with a compensating transfer rather than by editing history.

## Target users

Friends, family, and small groups who settle money directly — rent splits, shared trips, group
meals, and expense reimbursement — and who need a clear record afterwards without an account
at a bank.

## Capabilities

Eight end-to-end capabilities are implemented: seven business capabilities, plus authentication
and account security. The detailed functional specification for each is in
[`docs/requirements.md`](docs/requirements.md); the correctness constraints every one of them
must uphold are in [`docs/invariants.md`](docs/invariants.md).

| Capability | What it does |
|---|---|
| **Wallet & Balance Management** | One wallet per account in INR. The balance is never edited directly — it is the sum of that wallet's ledger entries, and the stored balance is re-verified against the derived one. The home screen shows the balance, currency, opening date, wallet ID, and recent activity. |
| **P2P Transfers** | Pay anyone by email address. Money moves only when both wallets exist, the payer has the funds, and neither edge of the transfer fails; the transfer and both ledger legs are written together or not at all. |
| **Idempotent Transfers** | Every transfer carries an `Idempotency-Key`. Replaying a request returns the original transfer and moves no further money, so a double-tapped button or a retried request is safe. A key in the reserved `refund:` namespace is rejected so a client cannot mint a refund key of its own. |
| **Money Requests** | Ask someone for money instead of sending it. Nothing moves until they approve. Approval pays through the same transfer engine as any other payment; a decline or an expiry settles the request without moving money. Settled requests are terminal and cannot be re-answered. |
| **Refunds & Reversals** | The person who *received* a payment can return all or part of it. A refund writes a compensating transfer and its own ledger legs, so the reversal is as traceable as the original. The amount is capped at what is still refundable, and the same idempotency rules apply. |
| **Batch Processing** | Build a list of payments, save it, then run it — creation and execution are two deliberate actions, so nothing moves until you say so. Items run one at a time, each with its own outcome, so one failure does not abandon the rest. A batch can only be run by the account that owns it, and re-running a finished batch moves nothing. |
| **Authentication & Account Security** | Email and password sign-in. Passwords are hashed with bcrypt, and a JWT bearer token carries the session and expires after 24 hours by default. Every financial route requires that token, and the backend re-reads the account from it on each request rather than trusting the token's contents. Ownership is enforced server-side on every financial resource, so one account can never read or move another's money. |
| **Analytics** | A reading of the account's own ledger over a chosen period: total sent, total received, transaction count, net movement, average amount, and a day-by-day volume series. Summarised server-side in MongoDB; nothing is computed in the browser. See [Analytics](#analytics) for its deliberate limits. |

### Authentication & Account Security

Sign-in is email and password. Passwords are hashed with bcrypt, and a JWT bearer token carries
the session, expiring after 24 hours by default. Every financial route requires that token, and
authorization is decided by ownership: each financial resource is matched against the signed-in
account on the server, so one account can never read or move another account's wallets,
transfers, requests, refunds, or batches. Restricting a route to a caller is therefore a
server-side decision, never a UI affordance.

Accounts also carry a `role` field whose values are `USER`, `MERCHANT`, and `ADMIN`, and every
seeded account is a `USER`. The product requirements define no behavior for a role, so no
capability above is differentiated by one — every account reaches exactly the same capabilities,
and access is decided entirely by ownership. `require_role()` exists in
`apps/shared/authentication.py` as the authorization guard for role-scoped rules, and is not
attached to any production route.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend framework | React 19 |
| Frontend build tool | Vite 8 |
| Frontend language | JavaScript (JSX), no TypeScript |
| Frontend styling | Plain CSS, with Roboto bundled locally via `@fontsource/roboto` |
| Backend framework | Django 5.1 |
| REST layer | Django REST Framework 3.15 |
| Cross-origin support | `django-cors-headers` |
| Database | MongoDB 8 |
| Object–document mapper | MongoEngine 0.29 |
| Authentication | JWT via PyJWT 2.10, password hashing via bcrypt 4.2 |
| Environment config | `python-dotenv` |
| Package manager | Bun (JavaScript), with a single `bun.lock` |
| Python environment | `uv`-managed virtualenv, Python 3.12, at `.venv/` |
| Task running | `concurrently`, for starting both servers together |

There is no relational database. `DATABASES` is empty in Django settings and MongoDB is the only
store. No charting, state-management, routing, or HTTP client library is used: the frontend is
plain React state and hooks with a small `fetch` wrapper, and the chart is hand-built from CSS.

## Repository structure

The backend is grouped by product domain; the frontend is grouped the same way, so a feature is
easy to follow end to end.

```text
.
├── frontend/                     React + Vite workspace
│   ├── src/
│   │   ├── App.jsx               Page navigation, session, and shared state
│   │   ├── main.jsx              Entry point
│   │   ├── styles.css            All styling, including the responsive rules
│   │   ├── features/             One directory per capability
│   │   │   ├── analytics/        Analytics section and its API client
│   │   │   ├── auth/             Sign-in form and its API client
│   │   │   ├── batch/            Batch form, batch list, and API client
│   │   │   ├── home/             Home screen and recent activity
│   │   │   ├── money_requests/   Request form, request list, and API client
│   │   │   ├── refunds/          Refund form, refunds section, and API client
│   │   │   ├── transfers/        Transfer form and its API client
│   │   │   └── wallet/           Balance card, history list, and API client
│   │   └── shared/               Cross-feature code: fetch client, shared UI
│   ├── vite.config.js            Dev server, port, and `/api` proxy
│   └── package.json
│
├── backend/                      Django project
│   ├── manage.py
│   ├── wallet_backend/           Django project config: settings, root URLs
│   ├── apps/                     One package per capability
│   │   ├── analytics/            Read-only projection over the ledger
│   │   ├── auth/                 Sign-in, session, accounts, roles
│   │   ├── batch/                Batch documents and processing
│   │   ├── money_requests/       Request documents and state machine
│   │   ├── refunds/              Refunds and their compensating transfers
│   │   ├── transfers/            The transfer engine
│   │   ├── wallet/               Wallets and the ledger
│   │   └── shared/               Cross-cutting: auth guards, errors,
│   │                            validation, documents, rendering
│   ├── scripts/
│   │   ├── seed.py               Clears collections and builds the baseline
│   │   ├── seed_data.py          The baseline definitions
│   │   └── mutation_check.py     Verifies the test suite catches regressions
│   └── tests/                    525 automated tests
│
├── docs/
│   ├── requirements.md           What each capability must do
│   └── invariants.md             The correctness constraints it must uphold
│
├── setup.sh                      Environment, MongoDB, and seed setup
├── package.json                  Root scripts and the Bun workspace
├── hackerrank.yml                Install and run configuration
└── README.md
```

## Prerequisites

| Requirement | Notes |
|---|---|
| **Bun** | The JavaScript package manager and script runner. The repository uses a single `bun.lock`. |
| **Python 3.12** | Used to create the backend virtualenv. |
| **uv** | Creates `.venv` and installs the Python dependencies. `setup.sh` calls it directly. |
| **MongoDB** | Must be reachable on `127.0.0.1:27017`. `setup.sh` starts a local `mongod` if one is not already listening, and fails with a clear message if it cannot. |

## Setup and run

From the repository root:

```bash
# Install JavaScript dependencies, create the Python environment,
# start MongoDB if needed, and seed the database.
bun install && bash setup.sh --seed

# Start the backend and frontend together.
bun start
```

`bun start` runs a setup pass first (`prestart`) and then starts both servers concurrently.
To run them separately:

```bash
bun run dev:backend    # Django on port 8000
bun run dev:frontend   # Vite on port 3000
```

To re-seed the database at any time:

```bash
bash setup.sh --seed
```

### Environment setup

No manual configuration is required. `setup.sh` copies each `.env.example` to its `.env`
where one exists, so the defaults are used as-is. To change them, edit the `.env` files
directly — they are gitignored.

| File | Purpose |
|---|---|
| `backend/.env` | `PORT`, `NODE_ENV`, `MONGODB_URI`, `JWT_SECRET`, `JWT_EXPIRES_IN` |
| `frontend/.env` | `VITE_API_URL`, the API base path |

`backend/.env.example` ships a development `JWT_SECRET`. **Set your own before exposing the
backend to anything other than your own machine**, since anyone holding that value can mint a
valid session token for any account.

## Ports and API

| Service | Port | Notes |
|---|---|---|
| Frontend (Vite) | **3000** | Configured in `frontend/vite.config.js` |
| Backend (Django) | **8000** | `bun run dev:backend` binds `0.0.0.0:8000` |
| MongoDB | 27017 | `mongodb://localhost:27017/wallet_db` |

The frontend reaches the backend on the **same origin**: it requests `/api/v1/...`, and Vite
proxies `/api` to `http://localhost:8000` during development. The base path comes from
`VITE_API_URL` in `frontend/.env`, which defaults to `/api/v1`. Because the proxy is
same-origin, the browser makes one origin's requests and no CORS preflight is needed in
development.

### API surface

Every route is prefixed with `/api/v1` and every route except health requires a bearer token.

| Route | Purpose |
|---|---|
| `GET /health` | Liveness and database connectivity. No authentication. |
| `POST /auth/login` | Exchange email and password for a JWT. |
| `GET /auth/session` | The signed-in account and its role. |
| `GET /wallet` | The account's wallet and balance. |
| `GET /wallet/transactions` | Paginated ledger history, via `limit` and `offset`. |
| `POST /transfers` | Send money. Requires an `Idempotency-Key` header. |
| `GET /money-requests` | Money requests, filtered by side and state. |
| `POST /money-requests` | Ask for money. |
| `POST /money-requests/{id}/approval` | Approve, paying through the transfer engine. |
| `POST /money-requests/{id}/decline` | Decline, moving no money. |
| `GET /refunds` | Refundable payments and refunds already raised. |
| `POST /refunds` | Refund all or part of a payment you received. |
| `GET /batches` | Batches owned by the account. |
| `POST /batches` | Save a batch of payments. |
| `POST /batches/{id}/processing` | Run a saved batch, one item at a time. |
| `GET /analytics` | Period totals and daily volume, from `?from=` and `?to=`. |

## Seed and demo data

`setup.sh --seed` runs `backend/scripts/seed.py`, which **clears every application collection**
before rebuilding the baseline, so a full start always produces a known state. The definitions
live in `backend/scripts/seed_data.py`.

The baseline is produced by running the **real services** — transfers are created through the
transfer engine, approvals are approved for real, refunds go through the refund service —
rather than being written straight into collections. The seeded history is therefore produced by
the same code path a live request takes, and cannot disagree with it.

### Demo accounts

All four accounts share one password: **`Wallet@2026`**

| Name | Email | Opening balance | Role |
|---|---|---|---|
| Aarav Mehta | `aarav.mehta@wallet.demo` | ₹2,450.00 | `USER` |
| Fatima Noor | `fatima.noor@wallet.demo` | ₹128.50 | `USER` |
| Grace Obi | `grace.obi@wallet.demo` | ₹0.00 | `USER` |
| Diego Santos | `diego.santos@wallet.demo` | ₹9,650.00 | `USER` |

Accounts are funded **only** by an opening credit recorded in the ledger. Transfers move money
between accounts but never create it, so those opening entries are the only source of funds in
the baseline.

**Grace Obi is deliberately left empty.** She has no balance, no entries, and no batches, so
signing in as her shows the product's genuine empty states rather than a populated screen.

### What the baseline contains

| Collection | Count | What it demonstrates |
|---|---|---|
| `useraccounts` | 4 | Four funded accounts |
| `wallets` | 4 | One wallet per account |
| `ledgerentries` | 21 | Opening credits plus both legs of every movement |
| `transfers` | 9 | 3 direct payments, 2 approved requests, and 4 refund reversals — all completed |
| `moneyrequests` | 6 | 2 `PENDING`, 2 `APPROVED`, 1 `DECLINED`, 1 `EXPIRED`, covering the asking and the answering side |
| `refunds` | 4 | One full give-back, one partial, and two small corrections |
| `batches` | 1 | A saved batch **waiting to be run** |

The seeded batch is intentionally left unprocessed, so running a batch is something you do
rather than something the baseline did for you, and so no seeded balance has to change to
accommodate it.

## MongoDB behavior

MongoDB is the only datastore. Django runs with an empty `DATABASES` mapping; every document is
a MongoEngine document with an explicit collection name.

Seven collections hold application state: `useraccounts`, `wallets`, `ledgerentries`,
`transfers`, `moneyrequests`, `refunds`, and `batches`.

Balances move through single-document conditional updates, so a balance can never go negative
and two concurrent payments cannot both spend the same funds. Four unique indexes carry rules
that must not bend:

| Index | Rule it enforces |
|---|---|
| `wallets` on `userId` | One wallet per account |
| `ledgerentries` on `(transactionId, walletId)` | One leg per wallet per operation, so a replayed leg cannot be recorded twice |
| `transfers` on `(senderUserId, idempotencyKey)` | One transfer per sender and key |
| `refunds` on `(initiatorUserId, idempotencyKey)` | One refund per initiator and key |

Because the ledger is append-only, a wallet's stored balance is always re-derivable from its own
entries. The seed asserts that equality, and the test suite asserts it on every wallet.

## Analytics

Analytics is a section inside Activity, as a fifth filter beside All / Sent / Received /
Refunds. It answers **"what did I do in this period?"** — not "who do I deal with?".

Given a period of `YYYY-MM-DD` dates — both optional, and when they are not both named the window
is derived from whichever is, defaulting to 30 days — it reports total sent, total received,
transaction count, net movement, average amount, and a zero-filled daily volume series.

| Metric | Definition |
|---|---|
| Total sent | Sum of the account's debit entries — outgoing transfers and refunds |
| Total received | Sum of the account's credit entries — incoming transfers and refunds |
| Net movement | Received minus sent |
| Transactions | Count of those entries — ledger entries, not attempts |
| Average amount | Total ÷ count, rounded down to whole paise |
| Daily volume | The same three figures per UTC day |

### Deliberate limits

These boundaries are design decisions, not gaps to be discovered later:

- **No top counterparties.** "Who I transact with most" needs transfers joined to accounts, and
  it answers a different question. The ranked list of people belongs with the history register,
  which already names them.
- **No success or failure rates.** A refused transfer leaves a transfer row in `FAILED` and *no*
  ledger entry, because no money moved. Counting failures would mean reading totals from
  transfers, which mixes two different populations and produces figures that are quietly wrong.
- **No refund counts or refund share.** That is a refund feature's arithmetic wearing an
  analytics hat.
- **The opening entry is excluded** from every figure, while remaining part of the balance. It
  funds the wallet rather than being paid into it, and excluding it is what keeps net movement
  exactly equal to the change the ledger records over the period.
- **One bucket per day, on UTC boundaries**, so the same movement lands in the same bucket for
  every reader. A bucket is labelled with its UTC date.
- **Both period endpoints are included, maximum 366 days**, which bounds the response to 366
  buckets.
- **Empty days are returned as zeros**, not omitted, so the shape of the period stays visible.
- **A refused period stays refused.** An invalid period keeps the range you typed and the
  message explaining why, rather than being quietly replaced with a default.

## Development and testing

Run from the repository root unless noted.

```bash
# Backend framework check
cd backend && ../.venv/bin/python manage.py check

# Full automated test suite (525 tests)
cd backend && ../.venv/bin/python manage.py test

# Verify the suite actually catches regressions: introduces 12 targeted
# mutations, confirms each is caught, and reverts them
cd backend && ../.venv/bin/python scripts/mutation_check.py

# Production build
cd frontend && bun run build

# Formatting check
cd frontend && bunx prettier --check src
```

The suite covers normal operations, validation failures, authorization and ownership failures,
insufficient-funds refusals, state transitions, duplicate requests and idempotency,
concurrency-sensitive behavior, ledger correctness, atomicity, and refund limits. Concurrency is
tested with real OS threads racing on the same wallet, not simulated.

## Architecture

Each capability follows the same path, so a feature can be followed end to end:

```text
URL  →  View  →  Schema  →  Service  →  Repository  →  MongoDB collection
```

| Layer | Responsibility |
|---|---|
| **URL** (`urls.py`) | Maps a path to a view. |
| **View** (`views.py`) | Authenticates the caller, reads the request, returns the response. Holds no business rules. |
| **Schema** (`schema.py`) | Validates and shapes request and response data. |
| **Service** (`services.py`) | The business rules — the state machine, the money, the invariants. |
| **Repository** (`repository.py`) | The only layer that talks to MongoDB. |
| **Collection** | `wallets`, `ledgerentries`, `transfers`, and so on. |

The frontend mirrors this with a per-feature API client (`*.api.js`) that owns one endpoint, and
components that own their own loading, empty, validation, success, and error states.

`apps/analytics` is the one deliberate exception: it has no documents of its own, because it is
a read-only projection over the wallet app's `ledgerentries`.

Errors are handled centrally. Each capability names its error codes in `constants.py`, and
`apps/shared/errors.py` renders them, so a failure answers with a code and a message rather than
a stack trace.

## Design decisions worth knowing

- **The ledger is the source of truth.** A balance is a materialized value that is always
  re-derivable from its entries, and the equality is asserted rather than assumed.
- **Money never appears or vanishes.** Total debits equal total credits, and no operation leaves
  one ledger leg without the other. The seed proves it rather than assuming it: after building
  the baseline it re-checks that every wallet balance still sums to the total of the opening
  credits, and refuses to finish if it does not.
- **Completed history is never rewritten.** Corrections are new entries — a refund is a
  compensating transfer, not an edit.
- **Idempotency is enforced at the boundary**, with reserved key prefixes so one feature's keys
  cannot be minted by another.
- **The seed is deterministic.** The same counts and the same balances come out every time,
  which is what makes the baseline something a test can rely on.
