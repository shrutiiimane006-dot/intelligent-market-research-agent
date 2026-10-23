import json

import pytest

import app as app_module
from app.db import get_db


def test_environment_is_loaded_before_flask_configuration(monkeypatch, tmp_path):
    database_path = tmp_path / "loaded-from-env.sqlite"

    def fake_load_dotenv(_path):
        monkeypatch.setenv("SECRET_KEY", "environment-secret-value")
        monkeypatch.setenv("DATABASE_PATH", str(database_path))
        monkeypatch.setenv("FRONTEND_ORIGINS", "https://research.example")

    monkeypatch.setattr(app_module, "load_dotenv", fake_load_dotenv)
    application = app_module.create_app({"TESTING": True})

    assert application.config["SECRET_KEY"] == "environment-secret-value"
    assert application.config["DATABASE_PATH"] == str(database_path)
    assert application.config["FRONTEND_ORIGINS"] == {"https://research.example"}


def test_default_frontend_origins_include_localhost_and_loopback(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    application = app_module.create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE_PATH": ":memory:",
    })

    assert application.config["FRONTEND_ORIGINS"] == {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }


def test_production_requires_explicit_frontend_origins(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)

    with pytest.raises(RuntimeError, match="FRONTEND_ORIGINS"):
        app_module.create_app()


def test_production_rejects_local_http_frontend_origins(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://127.0.0.1:5173,https://research.example")

    with pytest.raises(RuntimeError, match="HTTPS deployment origins"):
        app_module.create_app()


def test_production_requires_secure_session_cookie(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://research.example")

    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE"):
        app_module.create_app({
            "SECRET_KEY": "s" * 48,
            "DATABASE_PATH": ":memory:",
            "SESSION_COOKIE_SECURE": False,
        })


def test_cross_site_cookie_configuration_requires_secure_cookie():
    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE"):
        app_module.create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_PATH": ":memory:",
            "SESSION_COOKIE_SAMESITE": "None",
            "SESSION_COOKIE_SECURE": False,
        })


def test_startup_recovers_interrupted_job_without_discarding_sources(monkeypatch, tmp_path):
    database_path = str(tmp_path / "recovery.sqlite")
    config = {
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE_PATH": database_path,
        "FRONTEND_ORIGINS": {"http://localhost:5173"},
    }
    application = app_module.create_app(config)
    client = application.test_client()
    token = client.get("/api/auth/csrf").json["csrf_token"]
    client.post(
        "/api/auth/register",
        json={
            "name": "Researcher",
            "email": "recovery@example.com",
            "password": "secure-password-123",
            "confirm_password": "secure-password-123",
        },
        headers={"X-CSRF-Token": token},
    )
    token = client.get("/api/auth/csrf").json["csrf_token"]
    monkeypatch.setattr("app.routes.launch_research", lambda *_args: None)
    response = client.post(
        "/api/research",
        json={"question": "Interrupted market query"},
        headers={"X-CSRF-Token": token},
    )
    job_id = response.json["job"]["id"]

    with application.app_context():
        db = get_db()
        db.execute(
            """INSERT INTO sources(job_id,title,url,snippet,retrieved_at)
               VALUES(?,?,?,?,?)""",
            (job_id, "Saved source", "https://example.com", "Saved evidence", "2026-10-21T00:00:00Z"),
        )
        db.execute(
            """UPDATE research_jobs SET status='running',current_stage='analysis',stages_json=?
               WHERE id=?""",
            ('[{"name":"analysis","status":"running"}]', job_id),
        )
        db.commit()

    restarted = app_module.create_app(config)
    with restarted.app_context():
        db = get_db()
        job = db.execute("SELECT status,error_summary,stages_json FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        report = db.execute("SELECT report_content,completion_status FROM research_reports WHERE job_id=?", (job_id,)).fetchone()
    assert job["status"] == "partial"
    assert "interrupted" in job["error_summary"].lower()
    assert report["completion_status"] == "partial"
    assert json.loads(report["report_content"])["sources"][0]["url"] == "https://example.com"
