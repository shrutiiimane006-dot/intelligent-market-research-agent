import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone

from flask import current_app, g


def get_db():
    if "db" not in g:
        path = current_app.config["DATABASE_PATH"]
        if path == ":memory:":
            if "test_db" not in current_app.config:
                current_app.config["test_db"] = sqlite3.connect(":memory:", check_same_thread=False)
                current_app.config["test_db"].row_factory = sqlite3.Row
                current_app.config["test_db"].execute("PRAGMA foreign_keys = ON")
            g.db = current_app.config["test_db"]
        else:
            g.db = sqlite3.connect(path, timeout=10)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None and current_app.config["DATABASE_PATH"] != ":memory:":
        db.close()


def init_db(app):
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with app.app_context():
        db = get_db()
        db.executescript(schema)
        db.commit()
        if app.config["DATABASE_PATH"] != ":memory:":
            close_db()


def recover_interrupted_jobs(app):
    timestamp = datetime.now(timezone.utc).isoformat()
    interruption = "Research was interrupted when the backend stopped. Retry this job to run it again."
    with app.app_context():
        db = get_db()
        jobs = db.execute(
            "SELECT * FROM research_jobs WHERE status IN ('pending','running')"
        ).fetchall()
        for job in jobs:
            try:
                stages = json.loads(job["stages_json"])
                if not isinstance(stages, list):
                    stages = []
            except (TypeError, json.JSONDecodeError):
                stages = []
            for stage in stages:
                if isinstance(stage, dict) and stage.get("status") == "running":
                    stage.update(status="failed", completed_at=timestamp, error=interruption)

            sources = db.execute(
                "SELECT id,title,url,retrieved_at FROM sources WHERE job_id=? ORDER BY id",
                (job["id"],),
            ).fetchall()
            status = "partial" if sources else "failed"
            db.execute(
                """UPDATE research_jobs SET status=?,current_stage=NULL,completed_at=?,error_summary=?,stages_json=?
                   WHERE id=?""",
                (status, timestamp, interruption, json.dumps(stages), job["id"]),
            )
            db.execute(
                """UPDATE agent_results SET status='failed',completed_at=?,error_summary=?
                   WHERE job_id=? AND status='running'""",
                (timestamp, interruption, job["id"]),
            )

            report = db.execute(
                "SELECT report_content FROM research_reports WHERE job_id=?",
                (job["id"],),
            ).fetchone()
            if report:
                try:
                    content = json.loads(report["report_content"])
                    if not isinstance(content, dict):
                        content = {}
                except (TypeError, json.JSONDecodeError):
                    content = {}
                content["completion_status"] = status
                limitations = content.get("limitations")
                if not isinstance(limitations, list):
                    limitations = []
                if interruption not in limitations:
                    limitations.append(interruption)
                content["limitations"] = limitations
                db.execute(
                    """UPDATE research_reports SET report_content=?,completion_status=?,updated_at=? WHERE job_id=?""",
                    (json.dumps(content), status, timestamp, job["id"]),
                )
            elif sources:
                content = {
                    "research_question": job["original_query"],
                    "research_scope": json.loads(job["research_scope"]),
                    "research_date": timestamp,
                    "completion_status": status,
                    "executive_summary": "Research was interrupted before AI analysis completed. No findings have been fabricated.",
                    "findings": [],
                    "opportunities": [],
                    "risks": [],
                    "recommendations": [],
                    "market_data": [],
                    "limitations": [interruption],
                    "sources": [dict(source) for source in sources],
                }
                title = job["original_query"][:100].strip() or "Market research report"
                db.execute(
                    """INSERT INTO research_reports(job_id,user_id,report_title,report_content,completion_status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (job["id"], job["user_id"], title, json.dumps(content), status, timestamp, timestamp),
                )
        db.commit()
        if app.config["DATABASE_PATH"] != ":memory:":
            close_db()
