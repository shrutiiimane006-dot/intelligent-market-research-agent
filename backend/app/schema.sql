PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    original_query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    research_scope TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed', 'partial')),
    current_stage TEXT,
    stages_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON research_jobs(user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_duplicate_jobs
    ON research_jobs(user_id, lower(original_query))
    WHERE status IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    snippet TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL,
    published_at TEXT,
    source_metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(job_id, url)
);

CREATE TABLE IF NOT EXISTS agent_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,
    result_data TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration REAL,
    error_summary TEXT,
    UNIQUE(job_id, agent_name)
);

CREATE TABLE IF NOT EXISTS research_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE REFERENCES research_jobs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_title TEXT NOT NULL,
    report_content TEXT NOT NULL,
    completion_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
