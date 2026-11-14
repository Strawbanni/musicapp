import pytest

from backend import create_app


@pytest.fixture
def client():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    return app.test_client()


def login(c, account, password):
    return c.post("/api/auth/login", json={"account": account, "password": password})


def test_register_login_and_profile(client):
    r = client.post("/api/auth/register", json={"username": "alice", "email": "a@x.com", "password": "secret1"})
    assert r.status_code == 201 and r.json["data"]["is_active"] is True
    assert client.get("/api/auth/me").json["data"]["username"] == "alice"

    assert client.post("/api/auth/register", json={"username": "alice", "email": "b@x.com", "password": "secret1"}).status_code == 409

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json["data"] is None
    assert login(client, "a@x.com", "wrong").status_code == 401
    assert login(client, "a@x.com", "secret1").status_code == 200

    r = client.put("/api/users/me/password", json={"old_password": "secret1", "new_password": "secret2"})
    assert r.status_code == 200
    assert login(client, "alice", "secret2").status_code == 200


def test_non_admin_forbidden(client):
    client.post("/api/auth/register", json={"username": "bob", "email": "b@x.com", "password": "secret1"})
    assert client.get("/api/users").status_code == 403


def test_admin_manage_and_deactivate(client):
    client.post("/api/auth/register", json={"username": "carol", "email": "c@x.com", "password": "secret1"})
    client.post("/api/auth/logout")
    assert login(client, "admin", "admin123").status_code == 200

    r = client.get("/api/users?keyword=carol")
    assert r.json["data"]["total"] == 1
    uid = r.json["data"]["items"][0]["user_id"]

    assert client.patch(f"/api/users/{uid}/status", json={"is_active": False}).status_code == 200
    assert client.get("/api/users?status=inactive").json["data"]["total"] == 1

    admin_id = client.get("/api/auth/me").json["data"]["user_id"]
    assert client.delete(f"/api/users/{admin_id}").status_code == 400  # can't delete self

    client.post("/api/auth/logout")
    assert login(client, "carol", "secret1").status_code == 403  # deactivated

    login(client, "admin", "admin123")
    assert client.post("/api/users", json={"username": "dave", "email": "d@x.com", "password": "secret1"}).status_code == 201
    assert client.delete(f"/api/users/{uid}").status_code == 200
    assert client.get(f"/api/users/{uid}").status_code == 404
