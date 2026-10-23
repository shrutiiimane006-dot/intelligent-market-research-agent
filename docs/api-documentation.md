# API documentation

Base URL in development: `http://127.0.0.1:5000/api`. JSON failures use:

```json
{"error":{"code":"invalid_query","message":"Enter a research question up to 2,000 characters."}}
```

## Sessions and CSRF

Sessions are signed cookies. The browser should send requests with credentials enabled. Before a state-changing request, fetch `GET /auth/csrf` and send its `csrf_token` in `X-CSRF-Token`. Registration and login rotate the session and CSRF token; fetch the token again after either succeeds.

### `POST /auth/register`

Request: `{ "name": "Ada Researcher", "email": "ada@example.com", "password": "at-least-12-characters", "confirm_password": "..." }`

Creates an account and signs in. Returns `201` with a minimal user object. Email addresses are unique case-insensitively. `confirm_password` is required and must match.

### `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `PATCH /auth/me`

Login accepts email and password and returns the current user. Logout clears the session. `me` returns the authenticated user's profile; PATCH accepts a non-empty `name`.

## Research

### `POST /research`

Requires authentication and CSRF token. Example:

```json
{
  "question": "What are the recent trends in electric bicycles?",
  "scope": {
    "industry": "Electric mobility",
    "geography": "Europe",
    "target_audience": "Urban commuters",
    "time_period": "2024-2026",
    "instructions": "Compare product positioning"
  }
}
```

Returns `202` and a job ID with initially pending stages. Submitting the same active question again returns `409 duplicate_submission`. The default limit is two active jobs per account; exceeding it returns `429 active_job_limit`. Status transitions are `pending`, `running`, and a terminal `completed`, `partial`, or `failed`. Per-stage states are `pending`, `running`, `completed`, `failed`, or `skipped`.

### `GET /research`, `GET /research/<id>`, `GET /research/<id>/status`

List supports `page` and `per_page` (maximum 50). Job detail returns saved stages. Status also returns source count and report availability. All records are filtered by the signed-in user.

### `GET /research/<id>/sources`, `GET /research/<id>/report`

Return stored source records and the saved report, respectively. A report that is not ready returns `409 report_not_ready`; a job with failed search and no evidence does not receive a fabricated report.

### `POST /research/<id>/retry`, `DELETE /research/<id>`

Retry only accepts a terminal job; it safely reruns the workflow and relies on job/source uniqueness to avoid duplicate source rows. Delete requires CSRF and removes the user's job and dependent records.

## Exports and dashboard

- `GET /research/<id>/export/pdf`: authenticated PDF report.
- `GET /research/<id>/export/csv`: findings, source records, numeric data, and completion status. Formula-leading spreadsheet values are escaped.
- `GET /dashboard/summary`: actual database totals.
- `GET /dashboard/recent`: the latest ten jobs.

## Health and providers

- `GET /health`: checks Flask and SQLite availability.
- `GET /health/providers` (authenticated): indicates Tavily key configuration and whether the configured Gemini model is listed as supporting text generation; it never returns credentials. Gemini status `model_supported` is a metadata check, not a successful generation or availability guarantee. Actual requests can still fail due to access changes, capacity, or timeouts.

Unknown API routes and unsupported API methods return a JSON `error` object with the appropriate HTTP status. API preflight responses allow `GET`, `POST`, `PATCH`, and `DELETE` for configured origins.
