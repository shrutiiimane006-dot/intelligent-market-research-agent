import json
import logging
import os
import threading
from datetime import datetime, timezone
from urllib.parse import urldefrag, urlsplit

from .agents import analyze_category, plan_query, review_findings, synthesize_report
from .db import get_db
from .providers import ProviderError, tavily_search, tavily_settings

logger = logging.getLogger(__name__)
_job_locks = {}
_locks_guard = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def _release_lock(job_id, lock):
    with _locks_guard:
        if _job_locks.get(job_id) is lock:
            del _job_locks[job_id]
        lock.release()


def launch_research(app, job_id):
    with _locks_guard:
        lock = _job_locks.setdefault(job_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError("A worker for this research job is already active.")
    thread = threading.Thread(target=_run_with_app, args=(app, job_id, lock), daemon=True)
    try:
        thread.start()
    except Exception:
        _release_lock(job_id, lock)
        raise


def _run_with_app(app, job_id, lock):
    with app.app_context():
        try:
            run_research(job_id)
        except Exception:
            logger.exception("Research job %s failed unexpectedly", job_id)
            db = get_db()
            job = db.execute("SELECT stages_json,current_stage FROM research_jobs WHERE id=?", (job_id,)).fetchone()
            if job:
                stages = json.loads(job["stages_json"])
                timestamp = now()
                for stage in stages:
                    if stage["status"] == "running":
                        stage.update(status="failed", completed_at=timestamp, error="Unexpected internal error.")
            db.execute(
                "UPDATE research_jobs SET stages_json=?, status='failed', current_stage=NULL, completed_at=?, error_summary=? WHERE id=?",
                (json.dumps(stages) if job else "[]", now(), "Research stopped because of an unexpected internal error.", job_id),
            )
            db.commit()
        finally:
            _release_lock(job_id, lock)


def _stage(db, job_id, name, status, error=None):
    row = db.execute("SELECT stages_json FROM research_jobs WHERE id=?", (job_id,)).fetchone()
    stages = json.loads(row["stages_json"])
    match = next((stage for stage in stages if stage["name"] == name), None)
    if match is None:
        match = {"name": name, "status": "pending", "started_at": None, "completed_at": None, "duration": None}
        stages.append(match)
    if status == "running":
        match.update(status="running", started_at=now(), error=None)
    else:
        match["status"] = status
        match["completed_at"] = now()
        match["error"] = error
        if match.get("started_at"):
            from datetime import datetime as dt
            match["duration"] = max(0, (dt.fromisoformat(match["completed_at"]) - dt.fromisoformat(match["started_at"])).total_seconds())
    db.execute(
        "UPDATE research_jobs SET stages_json=?, current_stage=?, status='running' WHERE id=?",
        (json.dumps(stages), name if status == "running" else None, job_id),
    )
    db.commit()


def _agent(db, job_id, name, status, result=None, error=None, started=None):
    finished = now() if status in {"completed", "failed", "skipped"} else None
    duration = None
    if started and finished:
        from datetime import datetime as dt
        duration = max(0, (dt.fromisoformat(finished) - dt.fromisoformat(started)).total_seconds())
    db.execute(
        """INSERT INTO agent_results(job_id, agent_name, status, result_data, started_at, completed_at, duration, error_summary)
           VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(job_id, agent_name) DO UPDATE SET
           status=excluded.status, result_data=excluded.result_data, started_at=excluded.started_at,
           completed_at=excluded.completed_at, duration=excluded.duration, error_summary=excluded.error_summary""",
        (job_id, name, status, json.dumps(result) if result is not None else None, started, finished, duration, error),
    )
    db.commit()


def _sources(db, job_id):
    rows = db.execute("SELECT * FROM sources WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return [dict(row) for row in rows]


def _save_report(db, job, content, status):
    timestamp = now()
    title = (job["original_query"][:100] or "Market research report").strip()
    db.execute(
        """INSERT INTO research_reports(job_id, user_id, report_title, report_content, completion_status, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
           report_content=excluded.report_content, completion_status=excluded.completion_status, updated_at=excluded.updated_at""",
        (job["id"], job["user_id"], title, json.dumps(content), status, timestamp, timestamp),
    )
    db.commit()


def _fallback_findings(analyses, review):
    if review:
        return [
            {
                "category": "reviewed evidence",
                "text": item["text"],
                "source_ids": item.get("source_ids", []),
                "verification_status": item.get("verification_status", "Unable to verify"),
            }
            for item in review.get("reviewed_findings", [])
        ]

    findings = []
    for category, result in analyses.items():
        for item in result.get("findings", []):
            findings.append({
                "category": category,
                "text": item["text"],
                "source_ids": item.get("source_ids", []),
                "verification_status": "Unable to verify",
            })
    return findings


def run_research(job_id):
    db = get_db()
    job_row = db.execute("SELECT * FROM research_jobs WHERE id=?", (job_id,)).fetchone()
    if not job_row:
        return
    job = dict(job_row)
    scope = json.loads(job["research_scope"])
    db.execute("UPDATE research_jobs SET status='running', started_at=COALESCE(started_at,?), error_summary=NULL WHERE id=?", (now(), job_id))
    db.commit()
    errors = []
    analyses = {}
    review = None
    final_report = None
    _stage(db, job_id, "planning", "running")
    planning_started = now()
    try:
        plan = plan_query(job["original_query"], scope)
        db.execute("UPDATE research_jobs SET normalized_query=? WHERE id=?", (plan["normalized_query"], job_id))
        _agent(db, job_id, "query_planner", "completed", plan, started=planning_started)
        _stage(db, job_id, "planning", "completed")
    except Exception:
        logger.exception("Planning failed for research job %s", job_id)
        errors.append("Query planning could not be completed.")
        _agent(db, job_id, "query_planner", "failed", error=errors[-1], started=planning_started)
        _stage(db, job_id, "planning", "failed", errors[-1])
        plan = {
            "search_queries": [job["original_query"]],
            "categories": ["market_trends", "competitors", "products", "customer_insights"],
        }

    _stage(db, job_id, "search", "running")
    search_succeeded = False
    search_started = now()
    try:
        limit, depth = tavily_settings()
        collected = {}
        for query in plan["search_queries"]:
            for result in tavily_search(query, limit, depth):
                if not isinstance(result, dict):
                    raise ProviderError("invalid_tavily_response", "Web search returned an invalid result.")
                url = result.get("url")
                if not isinstance(url, str):
                    continue
                url = urldefrag(url.strip()).url
                parsed_url = urlsplit(url)
                if (
                    parsed_url.scheme not in {"http", "https"}
                    or not parsed_url.hostname
                    or parsed_url.username
                    or parsed_url.password
                ):
                    continue
                collected.setdefault(url, result)
        for url, result in collected.items():
            db.execute(
                """INSERT OR IGNORE INTO sources(job_id,title,url,snippet,retrieved_at,published_at,source_metadata)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    job_id, str(result.get("title") or url)[:500], url[:2000],
                    str(result.get("content") or result.get("snippet") or "")[:10000],
                    now(),
                    str(result["published_date"])[:100] if isinstance(result.get("published_date"), (str, int, float)) else None,
                    json.dumps({"score": result.get("score"), "source": result.get("source")}),
                ),
            )
        db.commit()
        _agent(db, job_id, "source_retrieval", "completed", {"count": len(collected)}, started=search_started)
        _stage(db, job_id, "search", "completed")
        search_succeeded = True
        if not collected:
            errors.append("Live search completed but returned no usable sources.")
    except ProviderError as exc:
        errors.append(exc.message)
        _agent(db, job_id, "source_retrieval", "failed", error=exc.message, started=search_started)
        _stage(db, job_id, "search", "failed", exc.message)
    except Exception:
        logger.exception("Search failed for research job %s", job_id)
        errors.append("Live source retrieval failed unexpectedly.")
        _agent(db, job_id, "source_retrieval", "failed", error=errors[-1])
        _stage(db, job_id, "search", "failed", errors[-1])

    sources = _sources(db, job_id)
    if sources and search_succeeded:
        _stage(db, job_id, "analysis", "running")
        try:
            for category in plan.get("categories", []):
                analysis_started = now()
                result = analyze_category(category, job["original_query"], sources)
                analyses[category] = result
                _agent(db, job_id, category, "completed", result, started=analysis_started)
            _stage(db, job_id, "analysis", "completed")
        except ProviderError as exc:
            errors.append(exc.message)
            _agent(db, job_id, category, "failed", error=exc.message, started=analysis_started)
            _stage(db, job_id, "analysis", "failed", exc.message)
        except Exception:
            logger.exception("Analysis failed for research job %s", job_id)
            errors.append("AI specialist analysis failed unexpectedly.")
            _agent(db, job_id, category, "failed", error=errors[-1], started=analysis_started)
            _stage(db, job_id, "analysis", "failed", errors[-1])
    else:
        _stage(db, job_id, "analysis", "skipped", "Analysis requires successfully retrieved sources.")

    if analyses:
        _stage(db, job_id, "fact_check", "running")
        started = now()
        try:
            review = review_findings(job["original_query"], analyses, sources)
            _agent(db, job_id, "fact_checker", "completed", review, started=started)
            _stage(db, job_id, "fact_check", "completed")
        except ProviderError as exc:
            errors.append(exc.message)
            _agent(db, job_id, "fact_checker", "failed", error=exc.message, started=started)
            _stage(db, job_id, "fact_check", "failed", exc.message)
        except Exception:
            logger.exception("Fact-checking failed for research job %s", job_id)
            errors.append("AI fact-checking failed unexpectedly.")
            _agent(db, job_id, "fact_checker", "failed", error=errors[-1], started=started)
            _stage(db, job_id, "fact_check", "failed", errors[-1])
    else:
        _stage(db, job_id, "fact_check", "skipped", "Fact-checking requires completed specialist analysis.")

    if analyses and review:
        _stage(db, job_id, "synthesis", "running")
        started = now()
        try:
            final_report = synthesize_report(job["original_query"], scope, analyses, review, sources, errors)
            _agent(db, job_id, "synthesis", "completed", final_report, started=started)
            _stage(db, job_id, "synthesis", "completed")
        except ProviderError as exc:
            errors.append(exc.message)
            _agent(db, job_id, "synthesis", "failed", error=exc.message, started=started)
            _stage(db, job_id, "synthesis", "failed", exc.message)
        except Exception:
            logger.exception("Synthesis failed for research job %s", job_id)
            errors.append("AI report synthesis failed unexpectedly.")
            _agent(db, job_id, "synthesis", "failed", error=errors[-1], started=started)
            _stage(db, job_id, "synthesis", "failed", errors[-1])
    else:
        _stage(db, job_id, "synthesis", "skipped", "Synthesis requires completed analysis and fact-checking.")

    status = "completed" if final_report and not errors else ("partial" if sources or analyses else "failed")
    fallback_findings = _fallback_findings(analyses, review)
    supported_fallbacks = [
        finding for finding in fallback_findings
        if finding["verification_status"] in {"Verified against available source", "Partially supported"}
    ]
    report = final_report or {
        "executive_summary": (
            " ".join(item["text"] for item in supported_fallbacks[:3])
            if supported_fallbacks
            else "AI report synthesis did not complete. Any findings shown below are unverified specialist results."
        ),
        "executive_summary_source_ids": list(dict.fromkeys(
            source_id for item in supported_fallbacks[:3] for source_id in item["source_ids"]
        )),
        "findings": fallback_findings,
        "opportunities": [],
        "risks": [],
        "recommendations": [],
        "limitations": errors or ["No source-backed findings could be produced."],
        "verification_status": "incomplete",
    }
    report["research_question"] = job["original_query"]
    report["research_scope"] = scope
    report["research_date"] = now()
    report["completion_status"] = status
    report["agent_analyses"] = analyses
    report["fact_check"] = review
    report.setdefault("market_data", [])
    report["limitations"] = list(report.get("limitations", [])) + [e for e in errors if e not in report.get("limitations", [])]
    report["sources"] = [{"id": s["id"], "title": s["title"], "url": s["url"], "retrieved_at": s["retrieved_at"]} for s in sources]
    if sources or analyses:
        _save_report(db, job, report, status)
    db.execute(
        "UPDATE research_jobs SET status=?, completed_at=?, current_stage=NULL, error_summary=? WHERE id=?",
        (status, now(), "; ".join(errors)[:1000] or None, job_id),
    )
    db.commit()
