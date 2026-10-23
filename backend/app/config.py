import os
from urllib.parse import urlsplit


def load_config():
    production = os.environ.get("FLASK_ENV") == "production"
    frontend_origins = os.environ.get("FRONTEND_ORIGINS")
    if frontend_origins is None:
        if production:
            raise RuntimeError("FRONTEND_ORIGINS must be configured in production.")
        frontend_origins = "http://localhost:5173,http://127.0.0.1:5173"
    allowed_origins = {value.strip() for value in frontend_origins.split(",") if value.strip()}
    if production and (
        not allowed_origins
        or any(
            urlsplit(origin).scheme != "https"
            or urlsplit(origin).hostname in {"localhost", "127.0.0.1", "::1"}
            for origin in allowed_origins
        )
    ):
        raise RuntimeError("Production FRONTEND_ORIGINS must contain only HTTPS deployment origins.")
    return {
        "SECRET_KEY": os.environ.get("SECRET_KEY"),
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", "instance/research.db"),
        "FRONTEND_ORIGINS": allowed_origins,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": os.environ.get("SESSION_COOKIE_SAMESITE", "Lax"),
        "SESSION_COOKIE_SECURE": os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
        "PERMANENT_SESSION_LIFETIME": 60 * 60 * 12,
    }
