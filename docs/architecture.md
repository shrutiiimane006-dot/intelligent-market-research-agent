# Architecture

The React application calls a Flask REST API; provider credentials remain in the backend environment. Flask sessions use signed, HTTP-only cookies and a CSRF token returned by `/api/auth/csrf`. Every private research query is scoped to the session user ID.

## Research lifecycle

1. The API validates and persists the question and optional scope.
2. An in-process background worker persists stage transitions and plans a query directly from the user input.
3. Tavily is called with a bounded result limit and timeout. Valid HTTP(S) URLs are deduplicated by `(job_id, url)` and source metadata is persisted.
4. Gemini's official SDK verifies and calls the configured model for four evidence-oriented specialist analyses, review, and synthesis.
5. Numeric market data is retained only when the literal value and evidence quote appear in a cited source snippet. The UI charts only multi-value series with a shared unit; unsupported or qualitative data is never coerced into a numeric graph.
6. The report is persisted as completed only when the AI analysis, review, and synthesis complete. Search or AI failures produce an accurate partial/failed status and safe error summary; collected sources remain available.

The fact-check stage only reviews claims that appeared in specialist results. It downgrades unsupported “verified” labels when an exact quoted snippet is unavailable. Synthesis findings must map to reviewed claims, and the executive summary is assembled from supported reviewed claims rather than unverified generated prose.

## Storage

SQLite uses foreign keys, ownership references, a user-history index, and a uniqueness constraint for source URLs per job. Schema initialization occurs at Flask app creation. Research scope, stage state, provider result payloads, and report content use JSON text because the data is structured and SQLite does not need a separate service.

## Deployment considerations

The in-process worker is suitable for local development and a single Flask process. A production multi-worker environment requires a durable queue, worker supervision, rate limiting, TLS, and secure cookie configuration. Use a private persistent volume for SQLite or move to a managed database only when deployment needs warrant it.

At app startup, any pending/running jobs left by a stopped process are marked failed or partial; existing sources are retained and surfaced in a partial report. This restart recovery assumes one active Flask process. Do not run multiple independent workers against the same SQLite file without replacing the in-process worker and startup recovery with a durable queue.
