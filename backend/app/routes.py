import csv
import io
import json
import os
import re
import secrets
import sqlite3
from functools import wraps
from html import escape
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, make_response, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .orchestrator import launch_research, now
from .providers import ProviderError, verify_gemini_model

api = Blueprint("api", __name__)
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _error(code, message, status):
    return jsonify(error={"code": code, "message": message}), status


def _json_object():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def login_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return _error("authentication_required", "Please log in to continue.", 401)
        return function(*args, **kwargs)
    return wrapped


def _owned_job(job_id):
    return get_db().execute(
        "SELECT * FROM research_jobs WHERE id=? AND user_id=?",
        (job_id, session["user_id"]),
    ).fetchone()


def _job_payload(job):
    data = dict(job)
    data["research_scope"] = json.loads(data["research_scope"])
    data["stages"] = json.loads(data.pop("stages_json"))
    data.pop("user_id", None)
    return data


def _launch_job(app, job_id):
    try:
        launch_research(app, job_id)
        return True
    except RuntimeError:
        current_app.logger.exception("Could not start the research worker for job %s", job_id)
        db = get_db()
        message = "The research worker could not be started. Retry this job."
        db.execute(
            "UPDATE research_jobs SET status='failed',completed_at=?,current_stage=NULL,error_summary=? WHERE id=?",
            (now(), message, job_id),
        )
        db.commit()
        return False


@api.get("/auth/csrf")
def csrf_token():
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return jsonify(csrf_token=session["csrf_token"])


@api.post("/auth/register")
def register():
    body = _json_object()
    if body is None:
        return _error("invalid_input", "A JSON object is required.", 400)
    name = body.get("name")
    email = body.get("email")
    password = body.get("password")
    confirm = body.get("confirm_password")
    if not all(isinstance(value, str) for value in (name, email, password, confirm)):
        return _error("invalid_input", "Name, email, password, and password confirmation are required.", 400)
    name, email = name.strip(), email.strip().lower()
    if not name or len(name) > 120 or len(email) > 254 or not _EMAIL_PATTERN.fullmatch(email):
        return _error("invalid_input", "Enter a valid name and email address.", 400)
    if len(password) < 12 or len(password) > 256:
        return _error("weak_password", "Password must be between 12 and 256 characters.", 400)
    if confirm is not None and confirm != password:
        return _error("password_mismatch", "Passwords do not match.", 400)
    db = get_db()
    timestamp = now()
    try:
        cursor = db.execute(
            "INSERT INTO users(name,email,password_hash,created_at,updated_at) VALUES(?,?,?,?,?)",
            (name, email, generate_password_hash(password, method="scrypt"), timestamp, timestamp),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return _error("email_in_use", "An account with that email already exists.", 409)
    session.clear()
    session["user_id"] = cursor.lastrowid
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return jsonify(user={"id": cursor.lastrowid, "name": name, "email": email}), 201


@api.post("/auth/login")
def login():
    body = _json_object()
    if body is None:
        return _error("invalid_credentials", "Email or password is incorrect.", 401)
    email, password = body.get("email"), body.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or len(password) > 256:
        return _error("invalid_credentials", "Email or password is incorrect.", 401)
    user = get_db().execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email.strip(),)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return _error("invalid_credentials", "Email or password is incorrect.", 401)
    session.clear()
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return jsonify(user={"id": user["id"], "name": user["name"], "email": user["email"]})


@api.post("/auth/logout")
@login_required
def logout():
    session.clear()
    return jsonify(message="Logged out.")


@api.get("/auth/me")
@login_required
def current_user():
    user = get_db().execute(
        "SELECT id,name,email,created_at FROM users WHERE id=?",
        (session["user_id"],),
    ).fetchone()
    if not user:
        session.clear()
        return _error("authentication_required", "Please log in to continue.", 401)
    return jsonify(user=dict(user))


@api.patch("/auth/me")
@login_required
def update_profile():
    body = _json_object()
    if body is None:
        return _error("invalid_input", "A JSON object is required.", 400)
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        return _error("invalid_input", "Name must be between 1 and 120 characters.", 400)
    db = get_db()
    db.execute("UPDATE users SET name=?, updated_at=? WHERE id=?", (name.strip(), now(), session["user_id"]))
    db.commit()
    return current_user()


@api.get("/health")
def health():
    try:
        get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        current_app.logger.exception("Health check database query failed")
        return _error("database_unavailable", "Database is unavailable.", 503)
    return jsonify(status="ok", database="ok")


@api.get("/health/providers")
@login_required
def providers_health():
    gemini_status = "not_configured"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        try:
            verify_gemini_model()
            gemini_status = "model_supported"
        except ProviderError as exc:
            gemini_status = exc.code
    return jsonify(providers={
        "tavily": "configured" if os.environ.get("TAVILY_API_KEY", "").strip() else "not_configured",
        "gemini": gemini_status,
    })


@api.post("/research")
@login_required
def create_research():
    body = _json_object()
    if body is None:
        return _error("invalid_input", "A JSON object is required.", 400)
    query = body.get("question", body.get("original_query"))
    scope = body.get("scope", {})
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > 2000:
        return _error("invalid_query", "Enter a research question up to 2,000 characters.", 400)
    if not isinstance(scope, dict) or len(json.dumps(scope)) > 5000:
        return _error("invalid_scope", "Research scope must be a small JSON object.", 400)
    allowed_scope = {"industry", "geography", "target_audience", "time_period", "instructions"}
    if any(key not in allowed_scope or not isinstance(value, str) or len(value) > 500 for key, value in scope.items()):
        return _error("invalid_scope", "Research scope contains an invalid field or value.", 400)

    db = get_db()
    query = query.strip()
    db.execute("BEGIN IMMEDIATE")
    active = db.execute(
        """SELECT id FROM research_jobs WHERE user_id=? AND lower(original_query)=lower(?)
           AND status IN ('pending','running')
           ORDER BY created_at DESC LIMIT 1""",
        (session["user_id"], query),
    ).fetchone()
    if active:
        db.rollback()
        return jsonify(error={"code": "duplicate_submission", "message": "This research is already in progress.", "job_id": active["id"]}), 409
    try:
        active_limit = max(1, min(int(os.environ.get("MAX_ACTIVE_RESEARCH_JOBS", "2")), 10))
    except ValueError:
        db.rollback()
        current_app.logger.error("MAX_ACTIVE_RESEARCH_JOBS must be an integer.")
        return _error("server_configuration_error", "Research job limits are not configured correctly.", 500)
    active_count = db.execute(
        "SELECT COUNT(*) AS count FROM research_jobs WHERE user_id=? AND status IN ('pending','running')",
        (session["user_id"],),
    ).fetchone()["count"]
    if active_count >= active_limit:
        db.rollback()
        return _error("active_job_limit", "Wait for an active research job to finish before starting another.", 429)
    job_id = secrets.token_urlsafe(18)
    timestamp = now()
    stages = [
        {"name": name, "status": "pending", "started_at": None, "completed_at": None, "duration": None}
        for name in ("planning", "search", "analysis", "fact_check", "synthesis")
    ]
    try:
        db.execute(
            """INSERT INTO research_jobs(id,user_id,original_query,normalized_query,research_scope,status,stages_json,created_at)
               VALUES(?,?,?,?,?,'pending',?,?)""",
            (job_id, session["user_id"], query, query, json.dumps(scope), json.dumps(stages), timestamp),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        active = db.execute(
            """SELECT id FROM research_jobs WHERE user_id=? AND lower(original_query)=lower(?)
               AND status IN ('pending','running') ORDER BY created_at DESC LIMIT 1""",
            (session["user_id"], query),
        ).fetchone()
        if active:
            return jsonify(error={"code": "duplicate_submission", "message": "This research is already in progress.", "job_id": active["id"]}), 409
        raise
    if not _launch_job(current_app._get_current_object(), job_id):
        return jsonify(error={
            "code": "research_worker_unavailable",
            "message": "The research worker could not be started. Retry this job from history.",
            "job_id": job_id,
        }), 503
    return jsonify(job={
        "id": job_id,
        "status": "pending",
        "original_query": query,
        "created_at": timestamp,
        "stages": stages,
    }), 202


@api.get("/research")
@login_required
def list_research():
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = min(50, max(1, int(request.args.get("per_page", "20"))))
    except ValueError:
        return _error("invalid_pagination", "Page and per_page must be integers.", 400)
    db = get_db()
    total = db.execute("SELECT COUNT(*) AS count FROM research_jobs WHERE user_id=?", (session["user_id"],)).fetchone()["count"]
    rows = db.execute(
        "SELECT * FROM research_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (session["user_id"], per_page, (page - 1) * per_page),
    ).fetchall()
    return jsonify(items=[_job_payload(row) for row in rows], page=page, per_page=per_page, total=total)


@api.get("/research/<job_id>")
@login_required
def get_research(job_id):
    job = _owned_job(job_id)
    if not job:
        return _error("not_found", "Research job was not found.", 404)
    return jsonify(job=_job_payload(job))


@api.get("/research/<job_id>/status")
@login_required
def research_status(job_id):
    job = _owned_job(job_id)
    if not job:
        return _error("not_found", "Research job was not found.", 404)
    data = _job_payload(job)
    data["source_count"] = get_db().execute("SELECT COUNT(*) AS count FROM sources WHERE job_id=?", (job_id,)).fetchone()["count"]
    data["report_available"] = get_db().execute("SELECT 1 FROM research_reports WHERE job_id=?", (job_id,)).fetchone() is not None
    return jsonify(job=data)


@api.get("/research/<job_id>/sources")
@login_required
def research_sources(job_id):
    if not _owned_job(job_id):
        return _error("not_found", "Research job was not found.", 404)
    rows = get_db().execute("SELECT * FROM sources WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return jsonify(sources=[dict(row) for row in rows])


@api.get("/research/<job_id>/report")
@login_required
def research_report(job_id):
    if not _owned_job(job_id):
        return _error("not_found", "Research job was not found.", 404)
    report = get_db().execute(
        "SELECT report_title,report_content,completion_status,created_at,updated_at FROM research_reports WHERE job_id=? AND user_id=?",
        (job_id, session["user_id"]),
    ).fetchone()
    if not report:
        return _error("report_not_ready", "The research report is not available yet.", 409)
    data = dict(report)
    data["report_content"] = json.loads(data["report_content"])
    return jsonify(report=data)


@api.post("/research/<job_id>/retry")
@login_required
def retry_research(job_id):
    job = _owned_job(job_id)
    if not job:
        return _error("not_found", "Research job was not found.", 404)
    if job["status"] in {"pending", "running"}:
        return _error("job_active", "This research is already running.", 409)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    job = _owned_job(job_id)
    if not job or job["status"] in {"pending", "running"}:
        db.rollback()
        return _error("job_active", "This research is already running.", 409)
    try:
        active_limit = max(1, min(int(os.environ.get("MAX_ACTIVE_RESEARCH_JOBS", "2")), 10))
    except ValueError:
        db.rollback()
        current_app.logger.error("MAX_ACTIVE_RESEARCH_JOBS must be an integer.")
        return _error("server_configuration_error", "Research job limits are not configured correctly.", 500)
    active_count = db.execute(
        "SELECT COUNT(*) AS count FROM research_jobs WHERE user_id=? AND status IN ('pending','running')",
        (session["user_id"],),
    ).fetchone()["count"]
    if active_count >= active_limit:
        db.rollback()
        return _error("active_job_limit", "Wait for an active research job to finish before retrying.", 429)
    stages = json.loads(job["stages_json"])
    for stage in stages:
        stage.update(status="pending", started_at=None, completed_at=None, duration=None, error=None)
    try:
        db.execute(
            """UPDATE research_jobs SET status='pending',completed_at=NULL,current_stage=NULL,error_summary=NULL,stages_json=?
               WHERE id=?""",
            (json.dumps(stages), job_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return _error("duplicate_submission", "Another matching research job is already running.", 409)
    if not _launch_job(current_app._get_current_object(), job_id):
        return jsonify(error={
            "code": "research_worker_unavailable",
            "message": "The research worker could not be started. Retry this job from history.",
            "job_id": job_id,
        }), 503
    return jsonify(job={"id": job_id, "status": "pending", "stages": stages}), 202


@api.delete("/research/<job_id>")
@login_required
def delete_research(job_id):
    if not _owned_job(job_id):
        return _error("not_found", "Research job was not found.", 404)
    db = get_db()
    db.execute("DELETE FROM research_jobs WHERE id=? AND user_id=?", (job_id, session["user_id"]))
    db.commit()
    return jsonify(message="Research deleted.")


@api.get("/dashboard/summary")
@login_required
def dashboard_summary():
    row = get_db().execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                  SUM(CASE WHEN status IN ('pending','running') THEN 1 ELSE 0 END) AS in_progress,
                  SUM(CASE WHEN status IN ('failed','partial') THEN 1 ELSE 0 END) AS incomplete
           FROM research_jobs WHERE user_id=?""",
        (session["user_id"],),
    ).fetchone()
    return jsonify(summary={key: row[key] or 0 for key in ("total", "completed", "in_progress", "incomplete")})


@api.get("/dashboard/recent")
@login_required
def dashboard_recent():
    rows = get_db().execute(
        "SELECT * FROM research_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
        (session["user_id"],),
    ).fetchall()
    return jsonify(items=[_job_payload(row) for row in rows])


def _spreadsheet_safe(value):
    text = "" if value is None else str(value)
    if text.startswith(("\t", "\r", "\n")) or text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@api.get("/research/<job_id>/export/csv")
@login_required
def export_csv(job_id):
    job = _owned_job(job_id)
    if not job:
        return _error("not_found", "Research job was not found.", 404)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["record_type", "category", "title_or_finding", "url", "verification_status", "retrieved_at"])
    writer.writerow(["job_status", "", _spreadsheet_safe(job["status"]), "", "", ""])
    report = get_db().execute("SELECT report_content,completion_status FROM research_reports WHERE job_id=?", (job_id,)).fetchone()
    if report:
        data = json.loads(report["report_content"])
        writer.writerow(["report_status", "", _spreadsheet_safe(report["completion_status"]), "", "", ""])
        writer.writerow(["research_question", "", _spreadsheet_safe(data.get("research_question")), "", "", ""])
        writer.writerow(["research_date", "", _spreadsheet_safe(data.get("research_date")), "", "", ""])
        singular = {
            "findings": "finding",
            "opportunities": "opportunity",
            "risks": "risk",
            "recommendations": "recommendation",
        }
        for section in singular:
            for finding in data.get(section, []):
                if isinstance(finding, dict):
                    source_ids = ", ".join(str(value) for value in finding.get("source_ids", []) if type(value) is int)
                    writer.writerow([
                        singular[section],
                        _spreadsheet_safe(finding.get("category")),
                        _spreadsheet_safe(finding.get("text")),
                        _spreadsheet_safe(source_ids),
                        _spreadsheet_safe(finding.get("verification_status")),
                        "",
                    ])
        for item in data.get("market_data", []):
            if isinstance(item, dict):
                description = " ".join(str(item.get(field) or "") for field in ("value", "unit", "period", "geography"))
                source_ids = ", ".join(str(value) for value in item.get("source_ids", []) if type(value) is int)
                writer.writerow(["market_data", _spreadsheet_safe(item.get("metric")), _spreadsheet_safe(description), _spreadsheet_safe(source_ids), "", ""])
    sources = get_db().execute("SELECT title,url,retrieved_at FROM sources WHERE job_id=?", (job_id,)).fetchall()
    for source in sources:
        writer.writerow(["source", "", _spreadsheet_safe(source["title"]), _spreadsheet_safe(source["url"]), "", source["retrieved_at"]])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="research-{job_id}.csv"'
    return response


@api.get("/research/<job_id>/export/pdf")
@login_required
def export_pdf(job_id):
    if not _owned_job(job_id):
        return _error("not_found", "Research job was not found.", 404)
    row = get_db().execute("SELECT report_title,report_content,completion_status FROM research_reports WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return _error("report_not_ready", "The research report is not available yet.", 409)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        current_app.logger.exception("PDF export dependency is unavailable")
        return _error("export_unavailable", "PDF export is not configured on this server.", 503)
    report = json.loads(row["report_content"])
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=letter, title=row["report_title"], rightMargin=48, leftMargin=48)
    styles = getSampleStyleSheet()
    styles["BodyText"].wordWrap = "CJK"
    styles["Title"].wordWrap = "CJK"
    story = [Paragraph(escape(row["report_title"]), styles["Title"])]
    story += [Paragraph(f"Status: {escape(row['completion_status'])}", styles["Normal"]), Spacer(1, 12)]
    story.append(Paragraph("Research question", styles["Heading2"]))
    story.append(Paragraph(escape(str(report.get("research_question", ""))), styles["BodyText"]))
    story.append(Paragraph("Executive summary", styles["Heading2"]))
    summary_text = str(report.get("executive_summary", ""))
    summary_source_ids = report.get("executive_summary_source_ids", [])
    if isinstance(summary_source_ids, list):
        citations = [str(value) for value in summary_source_ids if type(value) is int]
        if citations:
            summary_text += f" [{', '.join(citations)}]"
    story.append(Paragraph(escape(summary_text), styles["BodyText"]))
    for section_name, key in (("Findings", "findings"), ("Opportunities", "opportunities"), ("Risks", "risks"), ("Recommendations", "recommendations")):
        story.append(Paragraph(section_name, styles["Heading2"]))
        items = report.get(key, [])
        if not items:
            story.append(Paragraph("No supported findings available.", styles["BodyText"]))
        for item in items:
            text = str(item.get("text", "")) if isinstance(item, dict) else str(item)
            if isinstance(item, dict):
                source_ids = [str(value) for value in item.get("source_ids", []) if type(value) is int]
                if source_ids:
                    text += f" [{', '.join(source_ids)}]"
                if item.get("verification_status"):
                    text += f" (Verification: {item['verification_status']})"
            story.append(Paragraph(escape(str(text)), styles["BodyText"]))
    market_data = report.get("market_data", [])
    if market_data:
        story.append(Paragraph("Numeric market data", styles["Heading2"]))
        numeric_rows = [[Paragraph("Metric", styles["BodyText"]), Paragraph("Value / period / geography", styles["BodyText"]), Paragraph("Sources", styles["BodyText"])]]
        for item in market_data:
            details = " ".join(str(item.get(field) or "") for field in ("value", "unit", "period", "geography"))
            numeric_rows.append([
                Paragraph(escape(str(item.get("metric", ""))), styles["BodyText"]),
                Paragraph(escape(details), styles["BodyText"]),
                Paragraph(escape(", ".join(str(value) for value in item.get("source_ids", []))), styles["BodyText"]),
            ])
        numeric_table = Table(numeric_rows, colWidths=[150, 270, 60], repeatRows=1)
        numeric_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey)]))
        story.append(numeric_table)
    story.append(Paragraph("Sources", styles["Heading2"]))
    source_rows = [[Paragraph("ID / title", styles["BodyText"]), Paragraph("URL", styles["BodyText"])]]
    for source in report.get("sources", []):
        source_id = source.get("id")
        title_text = f"[{source_id}] {source.get('title', '')}"
        url = str(source.get("url", ""))
        parsed = urlsplit(url)
        safe_url = (
            url
            if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
            else ""
        )
        url_text = f'<link href="{escape(safe_url)}" color="blue">{escape(url)}</link>' if safe_url else escape(url)
        source_rows.append([
            Paragraph(escape(str(title_text)), styles["BodyText"]),
            Paragraph(url_text, styles["BodyText"]),
        ])
    if len(source_rows) > 1:
        table = Table(source_rows, colWidths=[180, 300], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey)]))
        story.append(table)
    story.append(Paragraph("Limitations", styles["Heading2"]))
    for limitation in report.get("limitations", []):
        story.append(Paragraph(escape(str(limitation)), styles["BodyText"]))
    document.build(story)
    response = make_response(buffer.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="research-{job_id}.pdf"'
    return response
