import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, session
from werkzeug.exceptions import HTTPException

from .db import close_db, init_db, recover_interrupted_jobs
from .routes import api


def create_app(test_config=None):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    app = Flask(__name__)
    from .config import load_config
    app.config.update(load_config())
    if test_config:
        app.config.update(test_config)
    secret_key = app.config.get("SECRET_KEY")
    if not secret_key:
        if os.environ.get("FLASK_ENV") == "production":
            raise RuntimeError("SECRET_KEY must be configured in production.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    elif not isinstance(secret_key, str):
        raise RuntimeError("SECRET_KEY must be a string.")
    elif os.environ.get("FLASK_ENV") == "production" and len(secret_key) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters in production.")
    if os.environ.get("FLASK_ENV") == "production" and not app.config["SESSION_COOKIE_SECURE"]:
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled in production.")

    same_site = str(app.config["SESSION_COOKIE_SAMESITE"]).strip().title()
    app.config["SESSION_COOKIE_SAMESITE"] = same_site
    if same_site not in {"Lax", "Strict", "None"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None.")
    if same_site == "None" and not app.config["SESSION_COOKIE_SECURE"]:
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled when SESSION_COOKIE_SAMESITE is None.")

    database_value = app.config["DATABASE_PATH"]
    if not isinstance(database_value, str) or not database_value.strip():
        raise RuntimeError("DATABASE_PATH must be a non-empty path.")
    database_path = Path(database_value)
    if database_path != Path(":memory:"):
        database_path.parent.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)
    init_db(app)
    recover_interrupted_jobs(app)
    app.register_blueprint(api, url_prefix="/api")

    @app.before_request
    def apply_cors_and_csrf():
        origin = request.headers.get("Origin")
        allowed = app.config["FRONTEND_ORIGINS"]
        if origin and origin in allowed:
            g.cors_origin = origin
        elif origin and request.path.startswith("/api"):
            return jsonify(error={"code": "origin_not_allowed", "message": "This origin is not allowed."}), 403

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = session.get("csrf_token")
            if not token or request.headers.get("X-CSRF-Token") != token:
                return jsonify(error={"code": "csrf_failed", "message": "Refresh the page and try again."}), 403

    @app.after_request
    def add_security_headers(response):
        origin = getattr(g, "cors_origin", None)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api") else "no-cache"
        return response

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error={"code": "request_too_large", "message": "The request is too large."}), 413

    @app.errorhandler(Exception)
    def internal_error(error):
        if isinstance(error, HTTPException):
            if request.path.startswith("/api"):
                return jsonify(error={
                    "code": error.name.lower().replace(" ", "_"),
                    "message": error.description,
                }), error.code
            return error
        app.logger.exception("Unhandled request error")
        return jsonify(error={"code": "internal_error", "message": "The server could not complete the request."}), 500

    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    return app
