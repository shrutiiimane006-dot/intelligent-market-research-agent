from werkzeug.security import check_password_hash


def test_registration_hashes_password_and_creates_session(client, csrf):
    response = client.post(
        "/api/auth/register",
        json={"name": "Researcher", "email": "user@example.com", "password": "a-secure-password-123", "confirm_password": "a-secure-password-123"},
        headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 201
    assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie
    with client.application.app_context():
        from app.db import get_db
        stored = get_db().execute("SELECT password_hash FROM users").fetchone()["password_hash"]
    assert stored != "a-secure-password-123"
    assert check_password_hash(stored, "a-secure-password-123")
    assert client.get("/api/auth/me").json["user"]["email"] == "user@example.com"


def test_login_rejects_wrong_password(client, csrf, register_user):
    register_user(client, csrf)
    csrf = client.get("/api/auth/csrf").json["csrf_token"]
    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "wrong"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 401
    assert response.json["error"]["code"] == "invalid_credentials"


def test_mutating_request_requires_csrf(client):
    response = client.post("/api/auth/register", json={})
    assert response.status_code == 403


def test_registration_accepts_localhost_origin(client, csrf):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Localhost Researcher",
            "email": "localhost@example.com",
            "password": "a-secure-password-123",
            "confirm_password": "a-secure-password-123",
        },
        headers={"Origin": "http://localhost:5173", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 201
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"


def test_registration_rejects_unconfigured_origin_before_csrf(client, csrf):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Rejected Researcher",
            "email": "rejected@example.com",
            "password": "a-secure-password-123",
            "confirm_password": "a-secure-password-123",
        },
        headers={"Origin": "http://evil.example", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 403
    assert response.json["error"]["code"] == "origin_not_allowed"
    with client.application.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_allowed_origin_does_not_bypass_csrf_validation(client):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Missing CSRF",
            "email": "no-csrf@example.com",
            "password": "a-secure-password-123",
            "confirm_password": "a-secure-password-123",
        },
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 403
    assert response.json["error"]["code"] == "csrf_failed"
    assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5173"


def test_registration_rejects_invalid_email_and_non_object_json(client, csrf):
    invalid_email = client.post(
        "/api/auth/register",
        json={"name": "Researcher", "email": "bad-address", "password": "a-secure-password-123", "confirm_password": "a-secure-password-123"},
        headers={"X-CSRF-Token": csrf},
    )
    invalid_shape = client.post("/api/auth/register", json=["not", "an", "object"], headers={"X-CSRF-Token": csrf})
    assert invalid_email.status_code == 400
    assert invalid_shape.status_code == 400


def test_profile_update_and_logout(client, csrf):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Researcher",
            "email": "user@example.com",
            "password": "a-secure-password-123",
            "confirm_password": "a-secure-password-123",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201
    csrf = client.get("/api/auth/csrf").json["csrf_token"]
    update = client.patch(
        "/api/auth/me",
        json={"name": "Updated Researcher"},
        headers={"X-CSRF-Token": csrf},
    )
    assert update.status_code == 200
    assert update.json["user"]["name"] == "Updated Researcher"
    logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
