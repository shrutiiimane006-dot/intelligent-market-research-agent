# Intelligent Market Research Agent

An evidence-focused research workspace built with Flask, SQLite, React, and TypeScript. It retrieves live web results from Tavily, analyzes those results with Google's Gemini API, stores private research history, and generates cited partial or completed reports. Provider failures are surfaced as incomplete work; the application does not generate market data or citations as fallbacks.

## Current implementation

- Cookie-session authentication, password hashing, CSRF protection, per-user report ownership, and private SQLite persistence.
- Live Tavily retrieval and Gemini model validation / JSON analysis, fact-check, and synthesis integrations.
- Persisted job stages, source deduplication, partial reports, safe retries, and research history.
- Restart recovery marks interrupted jobs accurately and preserves any retrieved sources in a partial report.
- Per-account active-job limits and protected provider checks help prevent accidental provider overuse.
- Responsive React dashboard, query submission, actual backend job status, report citations, and provider configuration status.
- User-protected CSV and PDF exports. CSV values beginning with spreadsheet formula characters are escaped.
- Numeric market data is accepted only when its exact value and evidence quote are present in a cited retrieved snippet. Charts are shown only for multiple values with a shared unit; otherwise the UI displays a sourced table or explains that numeric data was unavailable.
- Backend and frontend automated test suites.

The query planner uses the submitted question and optional scope to make a focused search query. It does not infer missing market facts. The specialist analysis, review, and synthesis steps use Gemini with retrieved source snippets. A completed report requires all of those steps to succeed; otherwise the report is marked partial or failed.

## Architecture

- `backend/app/routes.py`: REST API, sessions, ownership enforcement, dashboard, downloads.
- `backend/app/orchestrator.py`: background job execution, persistent stage transitions, partial-result preservation.
- `backend/app/agents.py`: query planning and distinct specialist, review, and synthesis prompts.
- `backend/app/providers.py`: Tavily HTTP integration and official Google Gen AI SDK integration.
- `backend/app/schema.sql`: SQLite tables, constraints, indexes, and foreign keys.
- `frontend/src/App.tsx`: authentication, dashboard, research workflow, status tracking, history, settings, and report UI.
- `frontend/src/api.ts`: same-origin API client, cookie credentials, CSRF token, and downloads.

## Requirements

- Python 3.11 or newer. Python 3.14 is in use for initial implementation; dependency compatibility depends on current provider SDK releases.
- Node.js 20 or newer (current LTS recommended) and npm.
- A Tavily API key for live search and a Google Gemini API key with access to the configured model for AI analysis.

Tavily performs web search; Gemini analyzes the retrieved evidence. Keys are used by the backend only. API access, quotas, model names, and free tiers may change.

## Windows setup (PowerShell)

Open two PowerShell terminals from the project directory.

### Backend

```powershell
Set-Location .\backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env } else { Write-Host "backend\.env already exists; leaving it unchanged." }
```

Edit `backend\.env` and set the keys you have configured. Do not replace an existing `.env` file. Generate a random session secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set:

```dotenv
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=gemini-3.8-flash
TAVILY_API_KEY=your-tavily-key
SECRET_KEY=use-a-long-random-secret
```

Start the Flask API:

```powershell
python run.py
```

The SQLite database is initialized automatically at `backend\instance\research.db`. Health endpoint: `http://127.0.0.1:5000/api/health`.
For local development without an existing `FRONTEND_ORIGINS` override, the backend explicitly allows both `http://localhost:5173` and `http://127.0.0.1:5173`. If you already have `backend\.env`, preserve its secrets and set `FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173` if you use both hostnames; restart Flask after changing it. Do not add wildcard origins.

### Frontend

Install the current Node.js LTS release (which includes npm) from [nodejs.org](https://nodejs.org/en/download) or with Windows Package Manager:

```powershell
winget install --id OpenJS.NodeJS.LTS --exact
```

If the installer requests elevation, approve it. Open a new PowerShell window and verify `node -v` and `npm.cmd -v`; restart VS Code too if it was already open so its integrated terminals inherit the updated PATH. On PowerShell configurations that block scripts, use `npm.cmd` rather than the `npm.ps1` shim. `package-lock.json` records the resolved dependency versions.

```powershell
Set-Location .\frontend
npm.cmd install
npm.cmd test
npm.cmd run build
npm.cmd run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173`) or `http://127.0.0.1:5173`. Vite proxies `/api` to Flask during development. The frontend API client includes cookies and sends the session CSRF token on mutations. Local HTTP uses an HTTP-only, `SameSite=Lax` session cookie without the `Secure` attribute; this is limited to development. For a separate frontend origin, set `FRONTEND_ORIGINS` in the backend `.env` to an exact comma-separated allowlist. Production must explicitly configure HTTPS deployment origins in `FRONTEND_ORIGINS`, set `SESSION_COOKIE_SECURE=true`, use HTTPS, configure a strong `SECRET_KEY`, and serve the frontend/API behind an appropriately configured TLS reverse proxy. Local development origins are rejected in production. If the frontend and API are on different sites, set `SESSION_COOKIE_SAMESITE=None` as well as `SESSION_COOKIE_SECURE=true`.

There is currently no `lint` script in `frontend/package.json`; `npm run` lists the available scripts. Add a lint command only if a linter is deliberately added to the project.

### Provider credentials

Create a Tavily key at [Tavily](https://app.tavily.com/home) and a Gemini API key in [Google AI Studio](https://aistudio.google.com/app/apikey). Keep both keys only in `backend\.env` or a secret manager; never put them in frontend environment files, source code, screenshots, or commits. The tracked `backend\.env.example` intentionally has blank values. If a real key was ever placed in `.env.example` or another tracked file, revoke it at the provider and generate a replacement immediately; do not reuse the exposed key. Copy the example to `backend\.env` without replacing an existing file, then enter only the replacement keys locally:

```powershell
Set-Location .\backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env } else { Write-Host "backend\.env already exists; leaving it unchanged." }
notepad .env
```

Do not paste keys into chat or terminal commands that may be recorded in shell history. Provider checks are authenticated; missing or invalid keys are reported without returning the secret.

### Git for Windows

If Git is not installed, use the official [Git for Windows download](https://git-scm.com/download/win) or Windows Package Manager:

```powershell
winget install --id Git.Git --exact
```

Open a new PowerShell window after installation and verify with `git --version`. Run `git status --short --ignored` from the project directory to inspect tracked changes, untracked files, and ignored outputs. Do not initialize a repository if one already exists.

## Environment variables

| Variable | Required for | Description |
| --- | --- | --- |
| `SECRET_KEY` | Production backend | Flask session signing key. Do not reuse a public or development value. |
| `FLASK_ENV` | Production backend | Set to `production` to require a configured secret key at startup. |
| `TAVILY_API_KEY` | Live source retrieval | Tavily backend API key. |
| `GEMINI_API_KEY` | AI analysis | Google Gemini API key; never expose it to the frontend. |
| `GEMINI_MODEL` | AI analysis | Model identifier, defaults to `gemini-3.8-flash`. |
| `DATABASE_PATH` | Persistence | SQLite path; defaults to `instance/research.db`. |
| `FRONTEND_ORIGINS` | Cross-origin deployment | Allowed browser origins, comma-separated. |
| `SESSION_COOKIE_SECURE` | Production backend | Set to `true` when served over HTTPS. |
| `SESSION_COOKIE_SAMESITE` | Cookie policy | Defaults to `Lax`; set to `None` only for cross-site frontend/API deployment, with secure cookies enabled. |
| `MAX_SEARCH_RESULTS` | Search | Tavily result limit (clamped to 1–20; default 8). |
| `SEARCH_DEPTH` | Search | Tavily depth, defaults to `basic`. |
| `MAX_ACTIVE_RESEARCH_JOBS` | Resource use | Maximum simultaneous jobs per account (clamped to 1–10; default 2). |

`backend\.env.example` contains blank secret placeholders. Keep `.env` files out of source control.
When `FLASK_ENV=production`, `FRONTEND_ORIGINS` must be explicitly configured with HTTPS deployment origins, and `SESSION_COOKIE_SECURE=true`; local development origins are rejected.

## API overview

All JSON errors use `{ "error": { "code": "...", "message": "..." } }`.

- `GET /api/health`, `GET /api/health/providers`
- `GET /api/auth/csrf`, `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET|PATCH /api/auth/me`
- `POST|GET /api/research`, `GET /api/research/<id>`, `GET /api/research/<id>/status`
- `GET /api/research/<id>/sources`, `GET /api/research/<id>/report`
- `POST /api/research/<id>/retry`, `DELETE /api/research/<id>`
- `GET /api/research/<id>/export/pdf`, `GET /api/research/<id>/export/csv`
- `GET /api/dashboard/summary`, `GET /api/dashboard/recent`

Mutating requests require the session's `X-CSRF-Token` value from `/api/auth/csrf`. Session cookies are HTTP-only and `SameSite=Lax`. Research IDs are not treated as authorization: every private endpoint checks the authenticated owner. `GET /api/health/providers` requires authentication because it performs a remote Gemini model check.

## Tests

Backend, from `backend` with the virtual environment active:

```powershell
python -m pytest
```

Frontend, from `frontend`:

```powershell
npm.cmd install
npm.cmd test
npm.cmd run build
```

There is no frontend lint script currently. Ordinary automated tests do not require provider API keys. The backend suite uses mocks for provider failures and local application behavior; passing it does not prove live Tavily or Gemini credentials work.

## Verification performed

- Backend automated tests: **51 passed** with Python 3.14.8, including origin allow/reject, CSRF enforcement, registration/session behavior, provider error handling, and a deterministic API workflow covering job progress, history, evidence citations, report retrieval, and CSV/PDF exports.
- Mocked provider tests: **17 passed**, including invalid credentials, 503/504 handling, and bounded retries.
- Backend syntax compilation: passed; `pip check`: no broken requirements.
- Flask process startup and `GET /api/health`: passed against a temporary in-memory database.
- Authenticated API check: registration, provider status, and research submission behaved as expected. Live Tavily returned two usable results. Gemini generation did not complete due to provider capacity/deadline errors.
- Registration origin fix: Flask preflight returned HTTP 200 with the exact `http://127.0.0.1:5173` origin and credential headers. A credentialed request with the session cookie and CSRF token reached JSON payload validation (`invalid_input` for the deliberately empty test payload), proving origin and CSRF validation both passed without creating an account. The automated registration test confirms a valid registration succeeds with the same origin.
- Frontend: Node.js **v24.20.0** and npm **11.19.0** verified; `npm.cmd test` passed **3 tests**, including registration request credentials and CSRF token; `npm.cmd run build` passed, and `npm.cmd audit` reported **0 vulnerabilities**. Build output includes a non-fatal warning about the 629 kB minified JavaScript chunk. No lint script is defined in `package.json`.
- The npm lockfile was generated locally and records the resolved dependencies. Vitest was updated to patched 4.1.x; the frontend test now cleans up between renders, and the duplicate Dashboard navigation item was removed.
- Live research was tested using a temporary database: Tavily search completed and two sources were saved; Gemini analysis failed on provider 503/504 capacity/deadline errors, so the real job produced a partial report with source references and no fabricated findings. A complete AI-synthesized live report remains unverified. The Gemini model setting was updated to the model recommended by the provider. Browser-based end-to-end flow, persistence across a separate backend process restart, and PDF visual inspection were not run.
- Git is not installed in a known system or user location, and no `.git` metadata directory exists in the project root. Git for Windows was hash-verified by Windows Package Manager, but its installer requested administrator approval and the installation was cancelled. Git status was not run; approve the installer prompt if you want Git installed, then inspect status without initializing a repository.

## Troubleshooting

- **401 from a private endpoint:** log in again; cookies are session-based. The browser client automatically includes credentials.
- **403 CSRF error:** fetch `/api/auth/csrf` again after logging in or registering and retry the action with `X-CSRF-Token`.
- **"This origin is not allowed":** confirm the browser address uses `http://localhost:5173` or `http://127.0.0.1:5173`, make sure the backend `.env` does not override `FRONTEND_ORIGINS` with a different host/port, and restart Flask. Origin allowlisting and CSRF token validation are separate checks; do not disable either.
- **Origin rejected:** add the exact frontend origin (including scheme and port) to `FRONTEND_ORIGINS`, then restart Flask.
- **Search stage failed:** configure a valid `TAVILY_API_KEY`; inspect the persisted stage error and `/api/health/providers`. A configured key does not guarantee quota or endpoint availability.
- **AI analysis incomplete:** configure `GEMINI_API_KEY` and a text-generation model in `GEMINI_MODEL`; check model access and quota. Transient 503/504 provider capacity failures are retried a bounded number of times. Retrieved Tavily sources remain available when analysis fails.
- **PDF export unavailable:** install backend requirements in the active virtual environment, then restart Flask.
- **Port conflicts:** run Flask on an unused local port and update the Vite `/api` proxy in `frontend/vite.config.ts`.
- **`node` or `npm` is not recognized:** restart PowerShell/VS Code so the user-level Node.js installation is picked up from `PATH`; use `npm.cmd` in PowerShell if script execution policy blocks `npm.ps1`.

## Limitations and next steps

- Research currently runs in an in-process background thread. For a single-instance local application, this avoids extra infrastructure; production multi-worker deployments should use a durable job queue.
- Live provider keys are not included in the repository. Local credentials are configured in the ignored `backend\.env`; the latest real Tavily search returned two sources, while Gemini generation encountered provider 503/504 capacity/deadline failures. Retry a live query later to verify Gemini generation, quotas, source quality, and a complete synthesized report.
- The review agent is model-assisted evidence review, not independent human or legal verification. Treat confidence and verification labels as analytical signals, and inspect source links.
- Charts are intentionally not populated with invented numbers; the synthesis validates numeric evidence against the cited retrieved snippets before the frontend plots it.
- This initial build does not implement account deletion, password reset, multi-factor authentication, production rate limiting, or a durable distributed task queue.
