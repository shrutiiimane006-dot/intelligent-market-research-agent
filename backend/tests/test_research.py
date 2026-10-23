import json

from app.db import get_db
from conftest import register


def _authenticated(client, csrf):
    register(client, csrf)
    return client.get("/api/auth/csrf").json["csrf_token"]


def test_research_creation_is_real_and_duplicate_is_blocked(client, csrf, monkeypatch):
    csrf = _authenticated(client, csrf)
    monkeypatch.setattr("app.routes.launch_research", lambda _app, _job: None)
    payload = {"question": "Market trends for electric bikes", "scope": {"geography": "Europe"}}
    response = client.post("/api/research", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 202
    job_id = response.json["job"]["id"]
    duplicate = client.post("/api/research", json=payload, headers={"X-CSRF-Token": csrf})
    assert duplicate.status_code == 409
    assert duplicate.json["error"]["job_id"] == job_id
    detail = client.get(f"/api/research/{job_id}").json["job"]
    assert detail["research_scope"]["geography"] == "Europe"
    assert all(stage["status"] == "pending" for stage in detail["stages"])


def test_research_records_are_private_to_their_owner(app, client, csrf, monkeypatch):
    csrf = _authenticated(client, csrf)
    monkeypatch.setattr("app.routes.launch_research", lambda _app, _job: None)
    response = client.post(
        "/api/research",
        json={"question": "Private market research"},
        headers={"X-CSRF-Token": csrf},
    )
    job_id = response.json["job"]["id"]
    client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    csrf = client.get("/api/auth/csrf").json["csrf_token"]
    register(client, csrf, "other@example.com")
    assert client.get(f"/api/research/{job_id}").status_code == 404
    assert client.get(f"/api/research/{job_id}/export/csv").status_code == 404


def test_health_and_dashboard_summary(client, csrf, register_user):
    assert client.get("/api/health").json == {"database": "ok", "status": "ok"}
    assert client.get("/api/dashboard/summary").status_code == 401
    assert client.get("/api/health/providers").status_code == 401
    register_user(client, csrf)
    assert client.get("/api/dashboard/summary").json["summary"]["total"] == 0


def test_provider_health_does_not_claim_gemini_generation_is_available(client, csrf, register_user, monkeypatch):
    register_user(client, csrf)
    monkeypatch.setenv("GEMINI_API_KEY", "test-configured")
    monkeypatch.setenv("TAVILY_API_KEY", "test-configured")
    monkeypatch.setattr("app.routes.verify_gemini_model", lambda: "test-model")

    response = client.get("/api/health/providers")

    assert response.status_code == 200
    assert response.json["providers"] == {
        "gemini": "model_supported",
        "tavily": "configured",
    }


def test_empty_successful_search_reports_a_clear_failure(app, client, csrf, monkeypatch):
    _authenticated(client, csrf)
    token = client.get("/api/auth/csrf").json["csrf_token"]
    monkeypatch.setattr("app.routes.launch_research", lambda _app, _job: None)
    response = client.post(
        "/api/research",
        json={"question": "Empty search result"},
        headers={"X-CSRF-Token": token},
    )
    job_id = response.json["job"]["id"]
    monkeypatch.setattr("app.orchestrator.tavily_search", lambda *_args: [])
    with app.app_context():
        from app.orchestrator import run_research
        run_research(job_id)
        db = get_db()
        job = db.execute("SELECT status,error_summary FROM research_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert "no usable sources" in job["error_summary"].lower()


def test_api_unknown_route_uses_consistent_json_error(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.is_json
    assert response.json["error"]["code"] == "not_found"


def test_cors_preflight_includes_patch(client):
    response = client.options(
        "/api/auth/me",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert response.status_code == 200
    assert "PATCH" in response.headers["Access-Control-Allow-Methods"]
    assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert "X-CSRF-Token" in response.headers["Access-Control-Allow-Headers"]


def test_per_user_active_job_limit_is_enforced(client, csrf, monkeypatch):
    _authenticated(client, csrf)
    token = client.get("/api/auth/csrf").json["csrf_token"]
    monkeypatch.setenv("MAX_ACTIVE_RESEARCH_JOBS", "1")
    monkeypatch.setattr("app.routes.launch_research", lambda *_args: None)
    first = client.post(
        "/api/research",
        json={"question": "First active query"},
        headers={"X-CSRF-Token": token},
    )
    second = client.post(
        "/api/research",
        json={"question": "Second active query"},
        headers={"X-CSRF-Token": token},
    )
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json["error"]["code"] == "active_job_limit"


def test_worker_start_failure_is_persisted_and_reported(client, csrf, monkeypatch):
    _authenticated(client, csrf)
    token = client.get("/api/auth/csrf").json["csrf_token"]

    def fail_to_start(*_args):
        raise RuntimeError("thread failure")

    monkeypatch.setattr("app.routes.launch_research", fail_to_start)
    response = client.post(
        "/api/research",
        json={"question": "Worker failure query"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 503
    assert response.json["error"]["code"] == "research_worker_unavailable"
    status = client.get(f"/api/research/{response.json['error']['job_id']}/status").json["job"]
    assert status["status"] == "failed"
    assert "could not be started" in status["error_summary"].lower()


def test_retry_restores_pending_and_blocks_duplicate_active_job(client, csrf, monkeypatch):
    csrf = _authenticated(client, csrf)
    monkeypatch.setattr("app.routes.launch_research", lambda _app, _job: None)
    created = client.post(
        "/api/research", json={"question": "Retry query"}, headers={"X-CSRF-Token": csrf}
    )
    job_id = created.json["job"]["id"]
    with client.application.app_context():
        db = get_db()
        db.execute(
            "UPDATE research_jobs SET status='failed',stages_json=? WHERE id=?",
            (json.dumps([{"name": "search", "status": "failed", "started_at": "start", "completed_at": "end"}]), job_id),
        )
        db.commit()
    retry = client.post(f"/api/research/{job_id}/retry", headers={"X-CSRF-Token": csrf})
    assert retry.status_code == 202
    duplicate = client.post(
        "/api/research", json={"question": "Retry query"}, headers={"X-CSRF-Token": csrf}
    )
    assert duplicate.status_code == 409
    job = client.get(f"/api/research/{job_id}/status").json["job"]
    assert job["status"] == "pending"
    assert job["stages"][0]["status"] == "pending"


def test_spreadsheet_formula_values_are_escaped():
    from app.routes import _spreadsheet_safe
    assert _spreadsheet_safe("=SUM(A1:A2)") == "'=SUM(A1:A2)"
    assert _spreadsheet_safe("  =1+1") == "'  =1+1"
    assert _spreadsheet_safe("normal title") == "normal title"


def test_csv_and_pdf_exports_contain_saved_report_data(client, csrf, register_user, monkeypatch):
    register_user(client, csrf)
    csrf = client.get("/api/auth/csrf").json["csrf_token"]
    monkeypatch.setattr("app.routes.launch_research", lambda _app, _job: None)
    response = client.post(
        "/api/research",
        json={"question": "Export test"},
        headers={"X-CSRF-Token": csrf},
    )
    job_id = response.json["job"]["id"]
    with client.application.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO sources(job_id,title,url,snippet,retrieved_at) VALUES(?,?,?,?,?)",
            (job_id, "=SUM(A1)", "https://example.com/source", "evidence", "2026-01-01T00:00:00Z"),
        )
        db.execute(
            """INSERT INTO research_reports(job_id,user_id,report_title,report_content,completion_status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                job_id, 1, "Export test",
                json.dumps({
                    "research_question": "Export test",
                    "executive_summary": "Saved summary",
                    "findings": [],
                    "opportunities": [],
                    "risks": [],
                    "recommendations": [],
                    "limitations": ["Partial results"],
                    "sources": [{"id": 1, "title": "Source", "url": "https://example.com/source"}],
                }),
                "partial", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
            ),
        )
        db.commit()
    csv_response = client.get(f"/api/research/{job_id}/export/csv")
    pdf_response = client.get(f"/api/research/{job_id}/export/pdf")
    assert csv_response.status_code == 200
    assert b"'=SUM(A1)" in csv_response.data
    assert b"partial" in csv_response.data
    assert pdf_response.status_code == 200
    assert pdf_response.data.startswith(b"%PDF")


def test_mocked_research_workflow_persists_citations_and_serves_report_exports(
    app, client, csrf, register_user, monkeypatch
):
    register_user(client, csrf)
    csrf = client.get("/api/auth/csrf").json["csrf_token"]
    monkeypatch.setattr("app.routes.launch_research", lambda *_args: None)
    monkeypatch.setattr(
        "app.orchestrator.tavily_search",
        lambda *_args: [{
            "title": "Market source",
            "url": "https://example.com/market-source",
            "content": "The source describes an observed market trend.",
        }],
    )
    finding = {
        "text": "The source describes an observed market trend.",
        "source_ids": [1],
        "verification_status": "Unable to verify",
        "confidence": 0.8,
    }
    monkeypatch.setattr(
        "app.orchestrator.analyze_category",
        lambda *_args: {"summary": "Source-backed analysis", "findings": [finding], "limitations": []},
    )
    monkeypatch.setattr(
        "app.orchestrator.review_findings",
        lambda *_args: {
            "reviewed_findings": [{
                **finding,
                "verification_status": "Verified against available source",
                "evidence_quotes": ["The source describes an observed market trend."],
            }],
            "contradictions": [],
            "limitations": [],
        },
    )
    monkeypatch.setattr(
        "app.orchestrator.synthesize_report",
        lambda *_args: {
            "executive_summary": "Ignored in favor of the reviewed claim.",
            "executive_summary_source_ids": [1],
            "findings": [{
                "category": "market_trends",
                **finding,
                "verification_status": "Verified against available source",
            }],
            "opportunities": [],
            "risks": [],
            "recommendations": [],
            "market_data": [],
            "limitations": [],
        },
    )

    created = client.post(
        "/api/research",
        json={"question": "Research a market trend"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 202
    job_id = created.json["job"]["id"]
    with app.app_context():
        from app.orchestrator import run_research
        run_research(job_id)

    status = client.get(f"/api/research/{job_id}/status")
    history = client.get("/api/research?per_page=50")
    sources = client.get(f"/api/research/{job_id}/sources")
    report = client.get(f"/api/research/{job_id}/report")
    csv_export = client.get(f"/api/research/{job_id}/export/csv")
    pdf_export = client.get(f"/api/research/{job_id}/export/pdf")

    assert status.json["job"]["status"] == "completed"
    assert status.json["job"]["report_available"] is True
    assert all(stage["status"] == "completed" for stage in status.json["job"]["stages"])
    assert any(item["id"] == job_id for item in history.json["items"])
    assert sources.json["sources"][0]["url"] == "https://example.com/market-source"
    saved_report = report.json["report"]["report_content"]
    assert saved_report["findings"][0]["source_ids"] == [1]
    assert saved_report["findings"][0]["verification_status"] == "Verified against available source"
    assert saved_report["executive_summary_source_ids"] == [1]
    assert csv_export.status_code == 200
    assert b"https://example.com/market-source" in csv_export.data
    assert pdf_export.status_code == 200
    assert pdf_export.data.startswith(b"%PDF")
