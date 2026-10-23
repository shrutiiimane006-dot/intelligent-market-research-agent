# Testing guide

## Unit and mocked integration tests

Run backend unit and integration tests with `python -m pytest` from `backend`. They verify authentication, password hashing, CSRF enforcement, environment loading, cookie configuration, SQLite persistence and restart recovery, ownership checks, job creation, duplicate and active-job protection, worker launch failure, dashboard data, and export safety. Frontend unit tests run with `npm.cmd test` from `frontend` in PowerShell.

These tests do not call Tavily or Gemini and are not evidence of live provider availability.

Backend tests also cover provider error mapping and bounded retry behavior with mocks, evidence-preserving partial failures, numeric evidence validation, and PDF/CSV export responses.

## Live provider checks

1. Configure `TAVILY_API_KEY`, `GEMINI_API_KEY`, and `GEMINI_MODEL` in `backend\.env`.
2. Start Flask and inspect `GET /api/health/providers`; `model_supported` confirms Gemini model metadata only, not successful generation. The endpoint never returns secret values.
3. Register a test account, start a research job in the UI, and inspect the status stages.
4. Open each source URL and compare generated claims with the original retrieved evidence.
5. Export the report in PDF and CSV and verify report status, source links, and data.
6. Restart the backend and verify the job/report is still present.
7. Register a second test account and verify it cannot access the first account's research.

Record provider, model, date, and result when documenting acceptance testing. Do not describe mocked tests as live provider success.

Latest local verification: 51 backend tests passed, including provider auth/timeout mapping, local-origin allow/reject, registration, CSRF enforcement, session-cookie behavior, production origin/cookie configuration, and a deterministic API workflow covering job progress, history, evidence citations, report retrieval, and CSV/PDF exports. Seventeen provider tests passed with mocks; backend syntax compilation passed, and `pip check` reported no broken requirements. Frontend's three Vitest tests and production build passed; `npm audit` reported zero vulnerabilities.

Latest live provider check: Tavily returned two usable results. Gemini model listing confirmed `gemini-3.8-flash` supports text generation, but live generation failed with provider 503/504 capacity/deadline errors. An actual research job in a disposable SQLite database completed search and persisted two source records, then ended `partial` when Gemini analysis failed; no AI findings or verified claims were fabricated. This exercised real search, source persistence, status transitions, partial report retrieval, and safe provider failure. Successful Gemini generation and a fully synthesized live report remain unverified.

## Failure behavior to verify

- Missing Tavily key: search stage fails without claiming a live search succeeded.
- Tavily timeout/API failure: the job is marked failed or partial; no fabricated sources are inserted.
- Missing Gemini key or model/API failure: retrieved sources are retained, AI stages do not become completed, and the report is marked partial.
- Retry: the job re-runs safely and source uniqueness prevents duplicate job/source URLs.
