# Reference Architecture

This document documents the ACTUAL implementation patterns used in this repository. It is intended as a precise implementation guide for building a new application following the same architectural style - without modifying this reference codebase.

## Project Overview

- **Type**: Fullstack calendar application (monorepo)
- **Frontend**: React 19 + Vite 8
- **Backend**: Django 5.1.5 + Django REST Framework 3.15.2
- **Database**: MongoDB via mongoengine 0.29.1
- **Auth**: JWT (PyJWT 2.10.1) with bcrypt 4.2.1 for password hashing
- **Monorepo**: Bun workspaces (root + frontend workspace)

---

## Dependency Versions

### Root (package.json)

- `concurrently`: 9.2.1 (devDependency)

### Frontend (frontend/package.json)

- `@fontsource/roboto`: 5.3.0
- `react`: 19.2.4
- `react-dom`: 19.2.4
- `@vitejs/plugin-react`: 6.1.1 (dev)
- `vite`: 8.2.2 (dev)

### Backend (backend/requirements.txt)

- `Django`: 5.1.5
- `djangorestframework`: 3.15.2
- `django-cors-headers`: 4.6.0
- `mongoengine`: 0.29.1
- `PyJWT`: 2.10.1
- `bcrypt`: 4.2.1
- `python-dotenv`: 1.0.1
- `tzdata`: 2024.2

---

## Scripts

### Root package.json

- `prestart`: `bash setup.sh --start` (pre-run hook)
- `start`: `concurrently "bun run dev:backend" "bun run dev:frontend"` - runs both servers
- `dev:frontend`: `cd frontend && bun run dev` (Vite dev server on port 3000)
- `dev:backend`: `cd backend && ../.venv/bin/python manage.py runserver 0.0.0.0:8000`
- `seed`: `cd backend && ../.venv/bin/python scripts/seed.py`

### Frontend package.json

- `dev`: `vite` (port 3000, proxied via Vite config)
- `build`: `vite build`
- `preview`: `vite preview`

### Backend

- `manage.py`: standard Django management (module: `calendar_backend.settings`)

---

## Configuration Files

- **Root**: `.editorconfig` (4-space indent, UTF-8, LF, trim trailing), `.prettierrc` (tabs with tabWidth 4, useTabs true, printWidth 140), `.gitignore`, `.gitattributes`, `.prettierignore`, `hackerrank.yml`, `setup.sh`, `bun.lock`
- **Frontend**: `vite.config.js` (proxy `/api` → `http://localhost:8000`, port 3000, allowedHosts includes `.internal`, sourcemaps enabled), `.env.example`
- **Backend**: `backend/.env.example`, `backend/requirements.txt`
- **Django settings**: `backend/calendar_backend/settings.py` - MongoDB connection via mongoengine, DRF custom renderer/exception handler, CORS allow all, no SQL DB configured (empty DATABASES), REST_FRAMEWORK configured with custom ApiRenderer and ApiExceptionHandler, JWT/secret settings read from env

---

## FRONTEND ANALYSIS

### Directory Structure

```
frontend/
├── public/
│   └── favicon.svg
├── src/
│   ├── features/           # Feature-based modules
│   │   ├── auth/
│   │   ├── calendar/
│   │   ├── events/
│   │   ├── insights/
│   │   ├── people/
│   │   └── profiles/
│   ├── shared/             # Cross-feature shared code
│   │   ├── api/
│   │   │   └── client.js  # Base API client
│   │   ├── components/     # Reusable UI components
│   │   │   ├── ConfirmationDialog.jsx
│   │   │   ├── MaterialIcon.jsx
│   │   │   ├── Modal.jsx
│   │   │   └── SelectMenu.jsx
│   │   └── utils/
│   │       ├── date.js
│   │       └── time-zone.js
│   ├── App.jsx             # Main application root
│   ├── main.jsx            # Entry point
│   └── styles.css          # Global styles
├── index.html
└── vite.config.js
```

**Actual pattern**: Feature-first organization under `src/features/`, with `shared/` for truly reusable cross-cutting concerns. Each feature owns its UI, API calls, and domain-specific logic.

### Feature Organization

Each feature folder contains (as observed):

- `*.jsx` component files (e.g., in calendar: `CalendarHeader.jsx`, `CalendarSidebar.jsx`, `MonthView.jsx`, `TimeGrid.jsx`, `DayColumn.jsx`, `CalendarList.jsx`, `MiniMonth.jsx`, `CalendarVisibilityToggle.jsx`, `CalendarLoadingState.jsx`)
- `{feature}.api.js` - API module for this feature (e.g., `calendar.api.js`, `event.api.js`, `auth.api.js`)
- Feature-specific components co-located with the feature

**Example features**: auth (login/session/switch-profile/logout UI), calendar (views, header, sidebar), events (editor, preview, search), people (availability/suggestions), profiles, insights.

### Component Organization

- **Co-location**: Components live within their feature directory (e.g., `features/calendar/MonthView.jsx`, `features/events/EventEditor.jsx`)
- **Shared components**: Pure reusable UI primitives in `shared/components/` (Modal, ConfirmationDialog, MaterialIcon, SelectMenu)
- **Root**: `App.jsx` is the orchestrator/top-level component (contains routing logic via view state, auth state, modals state management)
- **Props-driven**: Components take explicit props; callbacks passed down for interactions
- **Hooks-based**: Uses React hooks (`useState`, `useEffect`, `useCallback`, `useMemo`) extensively

### API Modules

**Pattern**: Per-feature API modules that import from shared base client.

**Example - features/auth/auth.api.js**:

```js
import { request } from "../../shared/api/client.js";
export const authApi = {
	login: (email, password) => request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
	session: () => request("/auth/session"),
	switchProfile: (profileId) => request("/auth/switch-profile", { method: "POST", body: JSON.stringify({ profileId }) }),
	logout: () => request("/auth/logout", { method: "POST" }),
};
```

**Example - features/calendar/calendar.api.js**: list/create/displayOnly/update/remove methods calling REST endpoints under `/calendars*`

All API calls go through `shared/api/client.js`.

### Shared Components

`shared/components/` contains minimal, generic UI primitives:

- **Modal.jsx**: Uses HTML `<dialog>` element. Manages showModal/close, autofocus, backdrop click to close, prevents default on cancel event. Exposes `onClose`, `label`/`labelledBy`, `className`.
- **ConfirmationDialog.jsx**: Confirmation modal UI
- **MaterialIcon.jsx**: Icon wrapper
- **SelectMenu.jsx**: Dropdown/select UI

These are framework-level shared UI, not feature-specific.

### Shared Utilities

**date.js**: Calendar date operations in DISPLAY_TIME_ZONE (UTC by design). Exports helpers like `dateKey`, `startOfDay`, `addDays`, `startOfWeek`, `startOfMonth`, `addMonths`, `getViewRange`, `moveCursor`, `formatHeading`, `formatTimeZoneOffset`, `roundToNextHalfHour`. Re-exports from `time-zone.js`. Key behavior: all calendar decisions in display zone for deterministic rendering.

**time-zone.js**: Timezone utilities using Intl API and UTC arithmetic. `DISPLAY_TIME_ZONE = "UTC"` (hardcoded). Functions: `partsAt`, `localDateKey`, `addCalendarDays`, `addCalendarMonths`, `dayOfWeek`, `zonedDateTime` (with 3-pass correction for DST), `minutesOf`, `weekdayOf`, `formatInZone`, `displayZoneOffsetLabel`. This mirrors backend time-zone utilities (comment indicates same intent).

### API Client

**shared/api/client.js** (`request()` function):

- Base URL from `import.meta.env.VITE_API_URL || "/api/v1"`
- Token management: stores `calendar-session-token` and `calendar-profile-token` in localStorage. `sessionToken`/`profileToken` module-level vars.
- `setSessionToken()`, `setProfileToken()`, `hasSessionToken()`, `hasProfileToken()`
- `request(path, options)`:
    - Adds `Content-Type: application/json` header
    - Adds `Authorization: Bearer <profileToken || sessionToken>` if any token exists (prefers profile token when present)
    - Parses JSON responses (checks content-type includes application/json)
    - Handles `204 No Content` → returns `null`
    - On error: extracts `payload?.error?.code` and `payload?.error?.message`. If 401 with codes `AUTH_REQUIRED`, `INVALID_TOKEN`, or `ACCOUNT_UNAVAILABLE`, clears both tokens and localStorage key `calendar-profile-id`, dispatches `calendar-session-expired` custom event. Throws Error with message.
    - On success: expects `payload.data` to exist; throws if not in expected shape.

**Actual auth/token flow**: Two-token model - workspace session token and profile token. Client sends whichever is available, preferring profile token. Backend uses middleware/decorators to require auth.

### State Management

- **Local component state**: Primarily `useState` in `App.jsx` (cursor, view, sidebar state, calendars, events, loading/error states, editor/preview/search/insights modals/states, etc.)
- **Derived state**: `useMemo` for computed values (visibleIds, range, insightRange)
- **Persistence**: Uses `localStorage` for view preference (`calendar-view`), sidebar state (`calendar-sidebar`), tokens, theme preference read on HTML load
- **Side effects**: `useEffect` for data loading (calendars, events, insights), shortcut handling, cursor sync
- **Callbacks**: `useCallback` to memoize handlers
- **No global state library** (Redux/Zustand/Context API beyond component props) - state is co-located in App and passed down

### Routing

- **Client-side "routing" via state**: No React Router observed. App uses view state (`day|week|month`), modal states (editor, preview, search panel/results, insights drawers), and auth flow states (`booting`, `needsWorkspace`, `needsProfile`, `ready`)
- **Initial route/boot**: Shows boot screen while checking auth; branches to WorkspaceLogin or ProfilePicker or main calendar
- **URL**: No URL-based routing - purely state-driven UI

### Naming Conventions

- **Components**: PascalCase filenames and component names (`CalendarHeader.jsx`, `EventEditor`, `WorkspaceLogin`)
- **API modules**: `{feature}.api.js` (camelCase with .api suffix)
- **Utils**: kebab-case or camelCase files (`date.js`, `time-zone.js`)
- **Variables/Functions**: camelCase
- **Constants**: UPPER_SNAKE_CASE (seen in time-zone.js: `DISPLAY_TIME_ZONE`, patterns)
- **CSS classes**: kebab-case (e.g., `calendar-loading-state`, `app-boot-brand`)
- **File extensions**: `.jsx` for components, `.js` for modules

### Feature Communication

- **Props drilling**: Parent (`App.jsx`) passes data and callbacks down to child components
- **Callback props**: Children invoke callbacks to mutate parent state (e.g., `onSave`, `onDelete`, `onSelect`, `onClose`)
- **Custom events**: Uses `CustomEvent` for cross-cutting concerns like session expiration (`calendar-session-expired`)
- **Shared utilities**: Features import from shared (utils, components, api client)
- **No direct cross-feature imports of internal component state** - communication via App-level orchestration

### Error Handling (Frontend)

- API client wraps errors: extracts server error message/code, handles 401 auth failures specially (clears tokens, emits event, throws)
- Components catch errors from API calls and set local error state (e.g., App sets `error` string, shows inline errors)
- No global error boundary observed in read files; errors displayed in UI contextually
- 204 responses handled as success (null)

### Authentication (Frontend)

- Token storage in localStorage (`calendar-session-token`, `calendar-profile-token`)
- Two-stage auth: workspace login (gets session token) → profile selection (switches to profile token)
- Client prefers profile token in Authorization header
- Session expiry detection: on 401 with specific codes, client auto-logs out and notifies via custom event
- `hasSessionToken()` checks for either token; `hasProfileToken()` checks profile token specifically
- Profile ID stored in localStorage as `calendar-profile-id` (referenced on logout/expiry)

## BACKEND ANALYSIS

### Directory Structure

```
backend/
├── apps/                    # Application modules (Django apps)
│   ├── auth/                # Auth/workspace accounts
│   ├── availability/        # Availability features
│   ├── calendars/           # Calendar management
│   ├── events/              # Events (with recurrence)
│   ├── insights/            # Insights/analytics
│   ├── people/              # People/profiles
│   ├── shared/              # Shared utilities, middleware-like helpers
│   │   ├── authentication.py # Auth decorators/middleware helpers
│   │   ├── demo_colors.py
│   │   ├── documents.py      # MongoEngine base classes
│   │   ├── errors.py         # Custom errors + exception handler
│   │   ├── mongo.py          # Mongo helpers
│   │   ├── numbers.py
│   │   ├── rendering.py      # DRF custom JSON renderer
│   │   ├── time_zone.py      # TZ utilities
│   │   ├── validation.py     # Validation helpers
│   │   └── views.py          # Shared views (health, 404)
│   └── __init__.py
├── calendar_backend/        # Django project
│   ├── __init__.py
│   ├── settings.py          # Django settings (MongoDB via mongoengine)
│   ├── urls.py              # Root URL routing
│   └── wsgi.py
├── public/                  # Static files served for landing
├── scripts/                 # Seed/utility scripts
│   ├── __init__.py
│   ├── seed.py              # Main seeding logic
│   └── seed_data.py          # Seed fixtures/data
├── .env.example
├── manage.py
└── requirements.txt
```

**Actual pattern**: Modular Django apps under `apps/`, each containing models, views, services, repository, urls, schema (and possibly constants). `shared/` app provides cross-cutting concerns. Project config in `calendar_backend/`.

### Root URL Routing

`calendar_backend/urls.py`:

```python
urlpatterns = [
    path("", landing_page),                        # Serves index.html from public/
    path("favicon.svg", favicon),
    path("api/v1/health", health),                 # Health check
    path("api/v1/", include("apps.auth.urls")),
    path("api/v1/", include("apps.people.urls")),
    path("api/v1/", include("apps.calendars.urls")),
    path("api/v1/", include("apps.events.urls")),
    path("api/v1/", include("apps.insights.urls")),
    path("api/v1/", include("apps.availability.urls")),
    re_path(r"^api/v1/(?P<path>.*)$", unknown_api_route),  # Catch-all API 404
    re_path(r"^.*$", not_found),                            # Catch-all 404
]
```

**Responsibilities**:

- Serves static landing page and favicon from `backend/public/`
- Mounts each app's URLs under `api/v1/`
- Provides catch-all routes for API unknown paths and general unknown paths
- Uses DRF `@api_view` decorators and shared auth/response patterns

Catch-all API route is wrapped with `@require_workspace_auth` and `@require_profile_context` decorators and returns `route_not_found`.

### App-level URL Routing

Each app defines its own `urls.py`. Examples:

**auth/urls.py**: `auth/login`, `auth/session`, `auth/switch-profile`, `auth/logout`
**people/urls.py**: `people/search` (search_people), `profiles` (list_profiles)
**calendars/urls.py**: `calendars`, `calendars/<str:calendar_id>`, `calendars/<str:calendar_id>/display-only`
**events/urls.py**: `events`, `events/search`, `events/<str:event_id>`, `events/<str:event_id>/response`
**insights/urls.py**: `insights/daily`
**availability/urls.py**: availability-related endpoints

**Naming**: RESTful style with plural resource names in paths. URL parameter naming uses snake_case consistently (e.g., `calendar_id`, `event_id`, `profile_id` in request/response payloads often camelCase like `profileId`).

### Views

**Pattern**: Function-based views using DRF decorators `@api_view([...])`. Views are thin - they:

1. Validate request data/query params via schema validators
2. Extract authenticated context (`request.account`, `request.profile`, `request.profile_id`) set by decorators
3. Call appropriate service function
4. Return DRF `Response` with wrapped data `{"data": ...}` or appropriate status codes (201 for create, 204 for delete)

**Examples**:

**auth/views.py**:

```python
@api_view(["POST"])
def login(request):
    email, password = validate_login(request.data)
    return Response({"data": services.login(email, password)})
```

**events/views.py** - method dispatching within single view function:

```python
@api_view(["GET", "POST"])
@require_workspace_auth
@require_profile_context
def events(request):
    if request.method == "POST":
        return Response({"data": services.create(validate_create(request.data), request.profile)}, status=201)
    return Response({"data": services.list_events(validate_list(request.query_params), request.profile_id)})
```

**events/event detail**:

```python
@api_view(["GET", "PATCH", "DELETE"])
@require_workspace_auth
@require_profile_context
def event(request, event_id):
    if request.method == "DELETE":
        services.remove(event_id, request.profile_id)
        return Response(status=204)
    if request.method == "PATCH":
        return Response({"data": services.update(event_id, validate_update(request.data), request.profile_id)})
    return Response({"data": services.get_by_id(event_id, request.profile_id)})
```

**Responsibilities**: HTTP protocol concerns only - method routing, status codes, request/response shaping. No business logic.

**Must NOT contain**: Business logic, data access logic, complex validation beyond schema delegation.

### Schemas

**Pattern**: Per-feature `schema.py` with validation functions. Uses shared validation utilities (`apps.shared.validation`). Each validator:

- Takes `body` or `query_params`
- Creates `Errors()` instance
- Uses `read_*` helpers (`read_string`, `read_email`, `read_date`, `read_number`, `read_boolean`, `read_option`, `read_identifier_list`, etc.)
- Calls `errors.raise_if_any()` to raise `ValidationError` if invalid
- Returns validated/normalized values (often tuples)

**Example - auth/schema.py**:

```python
def validate_login(body):
    errors = Errors()
    email = read_email(errors, body, "email", MAX_EMAIL_LENGTH)
    password = read_string(errors, body, "password", minimum=1, maximum=MAX_PASSWORD_LENGTH)
    errors.raise_if_any()
    return email, password
```

**Shared validation utilities** (`shared/validation.py`): Provides `Errors` class, type checking, constraints (min/max length/size), email/ID patterns, option validation, timezone validation, list validations. Raises `ValidationError` (subclass of `AppError`) with field/form error details.

### Services

**Pattern**: Business logic layer. Services orchestrate between repositories, other services, and shared utilities. They:

- Enforce business rules and invariants
- Handle authorization checks at business level where needed
- Transform data between layers
- Raise `AppError` for domain/business errors

**Examples**:

**auth/services.py**: Handles login (bcrypt check), JWT signing/decoding, token validation, session, profile switching. Interacts with `auth.repository` and `people.services`. Defines token types, expiry parsing.

**people/services.py**: Simple pass-throughs to repository plus helpers like `get_selected()` that validates all IDs exist and returns them in order.

**events/services.py** (large, ~416 lines): Contains complex logic for recurrence expansion (`expand_events`), ensuring chronology/recurrence validity, building event signatures, handling responses/overrides, visibility rules (owned calendars + participant events), search, create/update/delete with business validations. Calls multiple repositories (calendars, people) and recurrence utilities.

**Key characteristics**:

- Stateless functions
- Import other app services/repositories as needed
- Raise domain errors via `AppError` with specific codes/messages
- Return plain dicts/structures (MongoDB docs as dicts via `.as_pymongo()`)

**Layer calls**: Views → Services → Repositories (and Services may call other Services)

### Repositories

**Pattern**: Data access layer using MongoEngine. Repositories encapsulate MongoDB queries. They:

- Work with MongoEngine models
- Return query results as dicts (via `.as_pymongo()`) for serialization consistency
- Build query filters/conditions
- Handle ObjectId conversions

**Examples**:

**auth/repository.py**:

```python
def find_active_by_email(email):
    return WorkspaceAccount.objects(__raw__={"email": email, "active": True}).as_pymongo().first()

def find_active_by_id(account_id):
    return WorkspaceAccount.objects(__raw__={"_id": ObjectId(account_id), "active": True}).as_pymongo().first()
```

**events/repository.py**: More complex - builds visibility scope (owned calendars OR participant), overlapping range queries, search filters with `$or`/`$nor`, finders. Returns lists via `find_many()` with sorting.

**Traits**:

- Direct MongoEngine query construction (`__raw__` queries common)
- Sorting via `order_by(*SORT_ORDER)`
- Pagination via `.limit()`
- Collection access via `apps.shared.mongo.collection()` if needed

### Models

**Pattern**: MongoEngine document models. Use `TimestampedDocument` base class (from shared). Fields use MongoEngine types with db_field mappings to camelCase in DB.

**Base class**: `shared/documents.py`

```python
class TimestampedDocument(Document):
    meta = {"abstract": True}
    created_at = DateTimeField(db_field="createdAt")
    updated_at = DateTimeField(db_field="updatedAt")
    def save(self, *args, **kwargs):
        stamp(self)  # sets created_at/updated_at
        return super().save(*args, **kwargs)
```

**Examples**:

**auth/models.py** - `WorkspaceAccount`:

- Collection: `workspaceaccounts`
- Unique index on `email`
- Fields: name, email, password_hash (db: passwordHash), allowed_profile_ids (List of ObjectIds, db: allowedProfileIds), active

**people/models.py** - `Person`:

- Collection: `people`
- Indexes on email (unique), name, is_profile
- Embedded docs: `WorkingHours` (start_minute/end_minute), `BusyBlock` (title, start_at, end_at)
- Fields include avatar_color (regex validated), is_profile, time_zone, etc.

**events/models.py** - `Event`:

- Collection: `events`
- Indexes on (calendar_id, start_at, end_at) and type
- Complex embedded docs: `AttendeeResponse`, `Recurrence`, `RecurrenceResponseOverride`
- Many fields with choices/constants from `.constants`

**events/constants.py** (referenced): Defines enums/constraints like EVENT_TYPES, FREQUENCIES, RESPONSE_STATUSES, etc.

**Actual DB conventions**:

- MongoDB collections named lowercase (workspaceaccounts, people, events, calendars)
- Field names stored as camelCase in DB via `db_field` (e.g., createdAt, startAt, calendarId, participantIds)
- Python field names snake_case

### Shared Code

**authentication.py**: Auth helpers/decorators

- `read_token()`: Extracts Bearer token from Authorization header, raises `AppError(401, "AUTH_REQUIRED", ...)` if missing/invalid
- `resolve_profile()`: Validates profile in auth payload, checks access via `auth_service.allows_profile()`, fetches profile via `person_service.find_profile_by_id()`. Raises appropriate errors (401/403)
- `require_workspace_auth(view)`: Decorator that sets `request.account` and `request.auth_payload` by calling `auth_service.authenticate(read_token(request))`
- `require_profile_context(view)`: Decorator that sets `request.profile` and `request.profile_id` by calling `resolve_profile(request)`. Must be applied after workspace auth (uses `request.account`, `request.auth_payload`)

**errors.py**: Error handling

- `AppError(status_code, code, message, details=None)`: Base application error
- `ValidationError(AppError)`: Specialized for validation with form/field errors in details
- `api_exception_handler(exc, context)`: DRF exception handler. If `AppError`, returns `{"error": {...}}` with code/message/details. Falls back to DRF default handler, else logs and returns generic 500 with INTERNAL_ERROR code.

**rendering.py**: Custom JSON rendering

- `ApiEncoder(JSONEncoder)`: Serializes `ObjectId` → str, `datetime` → ISO 8601 with milliseconds and 'Z' suffix via `to_iso_string()`
- `ApiRenderer(JSONRenderer)`: Uses `ApiEncoder`. Ensures consistent API response serialization (ObjectIds as strings, datetimes in UTC with Z)

**validation.py**: Comprehensive validation utilities (as detailed above) - read helpers, type coercion, constraints, error collection.

**time_zone.py** (backend): Timezone utilities using `zoneinfo`. Similar to frontend but Python. Has `parts_at`, `local_date_key`, `zoned_date_time` with correction passes, `parse_instant`, `to_utc`, etc. Used for calendar calculations.

**documents.py**: MongoEngine base with timestamps
**mongo.py**: Helpers `collection(model)` and `to_dict(document)`
**demo_colors.py**: Demo data color mapping
**numbers.py**: Numeric helpers (round_half_up)
**views.py**: Shared API views - `health()` checks MongoDB ping via `get_db().command("ping")`, returns 200 if connected else 503 with degraded status; `route_not_found()`/`not_found()` raise 404 AppError

### Authentication

- **Method**: JWT tokens (HS256) via PyJWT
- **Token types**: `"workspace"` (session token after login) and `"profile"` (token scoped to a specific profile after switch)
- **Token payload** includes: `sub` (account ID), `type`, `iat`, `exp`, `aud`, `iss`, `profileId` (if profile token)
- **Secrets**: Read from settings (`JWT_SECRET`, `JWT_ISSUER="calendar-api"`, `JWT_AUDIENCE="calendar-app"`, `JWT_EXPIRES_IN` with duration parsing like "24h")
- **Password hashing**: bcrypt (`bcrypt.checkpw`, password stored as hash)
- **Flow**:
    1. `POST /auth/login` with email/password → validates → finds active account → checks bcrypt → returns `{account, token}` (workspace token, no profile)
    2. `GET /auth/session` (requires workspace auth) → returns account info
    3. `POST /auth/switch-profile` (requires workspace auth) → validates profileId, checks account allows profile, finds profile → returns `{profile, token}` (profile token)
    4. `POST /auth/logout` (requires workspace auth) → returns 204 (client-side token clearing)
- **Auth middleware/decorators**: Applied per view. `require_workspace_auth` authenticates token, loads account. `require_profile_context` requires profile token context and loads profile.

### Authorization

- **Profile access control**: `WorkspaceAccount.allowed_profile_ids` lists profile ObjectIds the account can access. `auth_service.allows_profile()` checks membership.
- **Resource visibility**:
    - Calendars: owned by profile context? (services use `profile_id` to scope)
    - Events: visible if in calendar owned by profile OR profile is participant (`participantIds` contains profile_id). See `events/repository.visibility_scope()`.
- **Profile-scoped operations**: Most API endpoints (after login) require both workspace auth and profile context. Profile context is required for operations that act in profile scope.

### Error Handling

- Centralized via custom DRF exception handler (`apps.shared.errors.api_exception_handler`)
- Domain errors throw `AppError(status_code, code, message, details)`
- Validation errors throw `ValidationError(form_errors, field_errors)` with details containing `formErrors` and `fieldErrors`
- Unhandled exceptions: logged to stdout (`print`) and returned as 500 with code `INTERNAL_ERROR`
- DRF's default exception handler is consulted as fallback
- API error response shape: `{"error": {"code": ..., "message": ..., "details": ... (optional)}}`

### Database Access

- **Database**: MongoDB (NoSQL)
- **ODM**: mongoengine 0.29.1
- **Connection**: Established in `settings.py` via `mongoengine.connect(host=MONGODB_URI, uuidRepresentation="standard")` on import
- **No Django ORM models** used for app data; uses MongoEngine Document classes. Django's `DATABASES` is empty dict.
- **Documents**: Inherit from `TimestampedDocument` (abstract) or `Document`. Timestamps auto-managed on save.
- **Queries**: Direct MongoEngine querysets, often using `__raw__` for precise MongoDB queries. Results converted to dicts via `.as_pymongo()`.
- **Indexes**: Defined in model `meta.indexes` (e.g., unique email, compound indexes on calendar ranges)
- **ObjectIds**: Handled as `bson.ObjectId`; serialized to strings in API responses via custom encoder

### Validation

- Schema-based validation at view layer using shared helpers
- Field-level and form-level error reporting
- Type coercion helpers (e.g., `coerce_number`)
- Pattern validation (email, ObjectId, color hex)
- Constraints: min/max lengths, ranges, enum options
- Timezone validation via `zoneinfo`

### Naming Conventions

**Python/Backend**:

- Modules/files: snake_case (`authentication.py`, `time_zone.py`)
- Classes: PascalCase (`WorkspaceAccount`, `AppError`, `ValidationError`)
- Functions/variables: snake_case
- Constants: UPPER_SNAKE_CASE (e.g., `TOKEN_ALGORITHM`, `DEFAULT_EXPIRY_SECONDS`)
- DB fields (Python): snake_case; mapped to camelCase in MongoDB via `db_field`
- Collections: lowercase (workspaceaccounts, people, events)
- URL parameter names: snake_case in paths (`calendar_id`)
- API payload field names: camelCase (e.g., `profileId`, `startAt`, `calendarId`) - matching what frontend sends

**Consistency**: Backend uses camelCase in API contracts (DB/storage and often in request/response field names as seen in validation like `read_string(..., "profileId")`), Python uses snake_case internally.

---

## Test Structure

**Observed**: No test files (`.test.*`, `.spec.*`) found in the explored codebase. No `tests/` directories visible. Test infrastructure not present in the actual implementation shown.

## Seed Structure

**Location**: `backend/scripts/`

- `seed.py`: Main seeding script. Responsibilities:
    - Sets up Django environment
    - Clears existing collections (Event, Calendar, Person, WorkspaceAccount) via `model.objects.delete()`
    - Creates demo workspace accounts with bcrypt-hashed passwords
    - Creates people/profiles with working hours, busy blocks, avatar colors
    - Creates calendars (owned, birthdays, etc.) per profile
    - Generates events: milestones, spread events across date ranges, recurring events (templates), invites attendees with response statuses
    - Uses demo data from `seed_data.py` and utilities from shared modules
- `seed_data.py`: Contains seed fixtures - PROFILE_ROWS, DEMO credentials, templates for recurring events, spread templates, milestones, etc. Uses constants like DEMO_TIME_ZONE, TEMPLATE_DAY_STRIDE, etc.
- Seed can be run via `bun run seed` (root) or directly `../.venv/bin/python scripts/seed.py` from backend
- Supports `DEMO_TODAY` env var to control "today" for seeding

## Files That Appear to be Starter/Contract Files

**Configuration/templates**:

- `.env.example` files (root context implied, backend has `.env.example`) - environment variable contracts
- `backend/public/index.html`, `backend/public/favicon.svg` - served by Django
- `frontend/index.html` - Vite entry with initial boot screen (shows loading state before React loads)
- `vite.config.js` - build/dev server contract
- `setup.sh` - environment setup script

**No explicit "starter" template files marked** - this appears to be a complete working implementation.

## Conventions to Preserve

### Frontend Conventions

1. **Feature-first structure**: Keep domain code under `src/features/{feature}/` with co-located components and `{feature}.api.js`
2. **Shared layer discipline**: Only truly cross-cutting code in `shared/` (api client, primitives, generic utils)
3. **API client pattern**: All HTTP calls through shared `request()` with token management and error normalization
4. **Display timezone**: All calendar calculations in fixed `DISPLAY_TIME_ZONE = "UTC"` (frontend and backend mirror this intent) for deterministic rendering
5. **Response shape**: Expect API responses wrapped as `{ data: ... }` or `{ error: ... }`; 204 on deletes
6. **Token model**: Two tokens (workspace session + profile). Prefer profile token, auto-logout on specific 401 codes
7. **Component style**: Functional components with hooks, props-driven, PascalCase names
8. **Modal pattern**: Use HTML `<dialog>` element as implemented in Modal component
9. **State location**: Orchestrate complex state in top-level (App), pass down via props; no global state management library
10. **Persistence**: Use localStorage for UI prefs and tokens with specific keys (`calendar-view`, `calendar-sidebar`, `calendar-session-token`, `calendar-profile-token`, `calendar-profile-id`)
11. **View as state**: Client-side view switching via state (not URL routing) for this app's UX

### Backend Conventions

1. **Layered architecture**: Views (HTTP) → Services (business logic) → Repositories (data access) → Models (schema). Each layer has single responsibility.
2. **Thin views**: Only validation, auth context extraction, delegation to services, response shaping
3. **Service isolation**: Business rules in services; services may call other services and repositories
4. **Repository encapsulation**: All MongoDB queries in repositories; return `.as_pymongo()` dicts
5. **Schema validation**: Centralized per-feature schema validators using shared `Errors` and read helpers
6. **Error handling**: Throw `AppError` subclasses with specific `code`, `message`, `status_code`; let exception handler format response
7. **Auth decorators**: Apply `@require_workspace_auth` then `@require_profile_context` as needed; decorators attach context to `request` (`account`, `auth_payload`, `profile`, `profile_id`)
8. **API response envelope**: All success responses use `{"data": ...}`; errors use `{"error": {...}}`. Renderer/encoder handle ObjectId/datetime serialization.
9. **MongoEngine patterns**: Use `TimestampedDocument`, `db_field` mappings to camelCase, define indexes in meta, prefer `__raw__` queries where precise
10. **Field naming**: Python snake_case internally, API/DB use camelCase (via db_field and API contracts)
11. **Catch-all routing**: Maintain API v1 catch-all with auth context returning 404, and general catch-all
12. **Settings**: MongoDB via mongoengine.connect (no SQL), custom DRF renderer and exception handler configured
13. **Health check**: Includes MongoDB connectivity check

### Cross-cutting Conventions

1. **Time representation**: UTC storage/handling, display in fixed zone (UTC) for consistency
2. **ObjectId handling**: Store as ObjectId in MongoDB, serialize as strings in API, validate with pattern
3. **Validation messages**: Use shared message helpers for consistency
4. **No file modifications rule**: This is reference code - treat as immutable contract
5. **Indentation**: 4 spaces (EditorConfig), Prettier configured with tabs (useTabs true) - follow existing style in each file context

---

## Summary

This codebase implements a layered, feature-organized architecture:

- **Frontend**: React app with feature modules + shared utilities/components, state-driven UI, token-based auth, custom API client with error handling
- **Backend**: Django REST API with apps, thin views, service/repository layers, MongoEngine ODM over MongoDB, JWT auth with profile scoping, centralized error handling and serialization

The architecture is pragmatic and consistent throughout - follow these exact patterns when building a new application based on this reference.
