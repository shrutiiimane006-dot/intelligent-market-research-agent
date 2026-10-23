import pytest

from app import create_app


@pytest.fixture
def app():
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE_PATH": ":memory:",
        "FRONTEND_ORIGINS": {"http://localhost:5173", "http://127.0.0.1:5173"},
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    return client.get("/api/auth/csrf").json["csrf_token"]


def register(client, csrf, email="owner@example.com"):
    return client.post(
        "/api/auth/register",
        json={"name": "Test Owner", "email": email, "password": "a-very-secure-password", "confirm_password": "a-very-secure-password"},
        headers={"X-CSRF-Token": csrf},
    )


@pytest.fixture
def register_user():
    return register
