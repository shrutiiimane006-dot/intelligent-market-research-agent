import json

import pytest

import app.orchestrator as orchestrator
from app.db import get_db
from app.orchestrator import launch_research, run_research
from app.providers import ProviderError


def test_launch_research_reserves_only_one_worker_per_job(app, monkeypatch):
    class WaitingThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(orchestrator.threading, "Thread", WaitingThread)
    launch_research(app, "job-in-progress")
    with pytest.raises(RuntimeError, match="already active"):
        launch_research(app, "job-in-progress")
    orchestrator._release_lock("job-in-progress", orchestrator._job_locks["job-in-progress"])
    assert "job-in-progress" not in orchestrator._job_locks


def _new_job(app, client, csrf, monkeypatch):
    client.get("/api/auth/csrf")
    token = client.get("/api/auth/csrf").json["csrf_token"]
    client.post(
        "/api/auth/register",
        json={"name": "Owner", "email": "owner@example.com", "password": "secure-password-123", "confirm_password": "secure-password-123"},
        headers={"X-CSRF-Token": token},
    )
    token = client.get("/api/auth/csrf").json["csrf_token"]
    monkeypatch.setattr("app.routes.launch_research", lambda _app, _job: None)
    response = client.post(
        "/api/research",
        json={"question": "Market trends for electric bikes"},
        headers={"X-CSRF-Token": token},
    )
    return response.json["job"]["id"], token


def test_ai_failure_preserves_deduplicated_sources_and_partial_report(app, client, csrf, monkeypatch):
    job_id, _token = _new_job(app, client, csrf, monkeypatch)
    result = {
        "title": "Industry outlook",
        "url": "https://example.com/outlook",
        "content": "The source reports recent market developments.",
        "published_date": "2026-01-01",
    }
    monkeypatch.setattr("app.orchestrator.tavily_search", lambda *_args: [result, result])
    monkeypatch.setattr(
        "app.orchestrator.analyze_category",
        lambda *_args: (_ for _ in ()).throw(ProviderError("missing_gemini_key", "AI analysis is not configured.")),
    )

    with app.app_context():
        run_research(job_id)
        db = get_db()
        job = db.execute("SELECT status,stages_json FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        count = db.execute("SELECT COUNT(*) AS count FROM sources WHERE job_id=?", (job_id,)).fetchone()["count"]
        report = db.execute("SELECT report_content,completion_status FROM research_reports WHERE job_id=?", (job_id,)).fetchone()

    stages = {stage["name"]: stage for stage in json.loads(job["stages_json"])}
    content = json.loads(report["report_content"])
    assert job["status"] == report["completion_status"] == "partial"
    assert count == 1
    assert stages["search"]["status"] == "completed"
    assert stages["analysis"]["status"] == "failed"
    assert stages["fact_check"]["status"] == stages["synthesis"]["status"] == "skipped"
    assert content["findings"] == []
    assert content["sources"][0]["url"] == "https://example.com/outlook"
    assert "AI analysis is not configured." in content["limitations"]


def test_search_failure_does_not_create_a_fabricated_report(app, client, csrf, monkeypatch):
    job_id, _token = _new_job(app, client, csrf, monkeypatch)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with app.app_context():
        run_research(job_id)
        db = get_db()
        job = db.execute("SELECT status,stages_json FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        report_count = db.execute("SELECT COUNT(*) AS count FROM research_reports WHERE job_id=?", (job_id,)).fetchone()["count"]
    stages = {stage["name"]: stage for stage in json.loads(job["stages_json"])}
    assert job["status"] == "failed"
    assert stages["search"]["status"] == "failed"
    assert report_count == 0


def test_missing_gemini_key_keeps_live_search_results_as_partial(app, client, csrf, monkeypatch):
    job_id, _token = _new_job(app, client, csrf, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.orchestrator.tavily_search",
        lambda *_args: [{"title": "Source", "url": "https://example.com/source", "content": "Evidence"}],
    )
    with app.app_context():
        run_research(job_id)
        db = get_db()
        job = db.execute("SELECT status,error_summary FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        source_count = db.execute("SELECT COUNT(*) AS count FROM sources WHERE job_id=?", (job_id,)).fetchone()["count"]
        report = db.execute("SELECT report_content FROM research_reports WHERE job_id=?", (job_id,)).fetchone()
    assert job["status"] == "partial"
    assert "not configured" in job["error_summary"].lower()
    assert source_count == 1
    assert json.loads(report["report_content"])["findings"] == []


def test_fact_check_failure_preserves_but_downgrades_specialist_findings(app, client, csrf, monkeypatch):
    job_id, _token = _new_job(app, client, csrf, monkeypatch)
    monkeypatch.setattr(
        "app.orchestrator.tavily_search",
        lambda *_args: [{"title": "Source", "url": "https://example.com/source", "content": "Evidence"}],
    )
    monkeypatch.setattr(
        "app.orchestrator.analyze_category",
        lambda category, *_args: {
            "summary": category,
            "findings": [{
                "text": "A specialist observation.",
                "source_ids": [1],
                "verification_status": "Unable to verify",
            }],
        },
    )
    monkeypatch.setattr(
        "app.orchestrator.review_findings",
        lambda *_args: (_ for _ in ()).throw(ProviderError("gemini_unavailable", "Review failed.")),
    )

    with app.app_context():
        run_research(job_id)
        report = get_db().execute(
            "SELECT report_content FROM research_reports WHERE job_id=?",
            (job_id,),
        ).fetchone()
    content = json.loads(report["report_content"])
    assert content["findings"][0]["text"] == "A specialist observation."
    assert content["findings"][0]["source_ids"] == [1]
    assert content["findings"][0]["verification_status"] == "Unable to verify"


def test_completed_pipeline_persists_report_and_actual_stage_states(app, client, csrf, monkeypatch):
    job_id, _token = _new_job(app, client, csrf, monkeypatch)
    monkeypatch.setattr(
        "app.orchestrator.tavily_search",
        lambda *_args: [{"title": "Source", "url": "https://example.com/source", "content": "Evidence"}],
    )
    monkeypatch.setattr(
        "app.orchestrator.analyze_category",
        lambda category, *_args: {"summary": category, "findings": []},
    )
    monkeypatch.setattr("app.orchestrator.review_findings", lambda *_args: {"reviewed_findings": []})
    monkeypatch.setattr(
        "app.orchestrator.synthesize_report",
        lambda *_args: {
            "executive_summary": "Summary based on retrieved evidence.",
            "findings": [],
            "opportunities": [],
            "risks": [],
            "recommendations": [],
            "limitations": [],
        },
    )

    with app.app_context():
        run_research(job_id)
        db = get_db()
        job = db.execute("SELECT status,stages_json FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        report = db.execute("SELECT completion_status FROM research_reports WHERE job_id=?", (job_id,)).fetchone()
    assert job["status"] == report["completion_status"] == "completed"
    assert all(stage["status"] == "completed" for stage in json.loads(job["stages_json"]))
