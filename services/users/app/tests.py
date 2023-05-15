"""Integration tests for the users service.

FastAPI + httpx AsyncClient pattern — equivalent to Django's APIClient but async.
TestClient (sync) wraps the ASGI app so no real server needed.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from fastapi.testclient import TestClient
from app.main import app, Base, engine

# pytest-asyncio setup — needed for async test functions
pytest_plugins = ("anyio",)


@pytest.fixture(scope="function", autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def client():
    """Sync test client — simpler for most tests."""
    with TestClient(app) as c:
        yield c


# ──────────────────────────────────────────
#  Registration
# ──────────────────────────────────────────

def test_register_creates_user(client):
    resp = client.post("/users", json={"email": "alice@test.com", "password": "secret123"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "alice@test.com"
    assert data["role"]  == "user"
    assert "id" in data


def test_register_duplicate_email_returns_409(client):
    payload = {"email": "bob@test.com", "password": "pass"}
    client.post("/users", json=payload)
    resp = client.post("/users", json=payload)
    assert resp.status_code == 409


# ──────────────────────────────────────────
#  Login
# ──────────────────────────────────────────

def test_login_returns_token(client):
    client.post("/users", json={"email": "carol@test.com", "password": "pw"})
    resp = client.post("/token", data={"username": "carol@test.com", "password": "pw"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password_returns_401(client):
    client.post("/users", json={"email": "dave@test.com", "password": "correct"})
    resp = client.post("/token", data={"username": "dave@test.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user_returns_401(client):
    resp = client.post("/token", data={"username": "ghost@test.com", "password": "x"})
    assert resp.status_code == 401


# ──────────────────────────────────────────
#  /users/me
# ──────────────────────────────────────────

def test_me_returns_user_for_valid_token(client):
    client.post("/users", json={"email": "eve@test.com", "password": "pw"})
    token_resp = client.post("/token", data={"username": "eve@test.com", "password": "pw"})
    token = token_resp.json()["access_token"]

    resp = client.get("/users/me", params={"token": token})
    assert resp.status_code == 200
    assert resp.json()["email"] == "eve@test.com"


def test_me_returns_401_for_invalid_token(client):
    resp = client.get("/users/me", params={"token": "not-a-real-token"})
    assert resp.status_code == 401


# ──────────────────────────────────────────
#  Health
# ──────────────────────────────────────────

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
