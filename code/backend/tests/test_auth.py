"""test_auth.py - 认证流程: 注册 / 登录 / refresh / me.

不 mock 数据库, 走真实 demo 数据库的 core.tenant + core.user 表.
注册测试用 user_test_<uuid>@example.com 邮箱 (test.local 是保留 TLD, Pydantic 拒).
"""
import uuid

from fastapi.testclient import TestClient


def test_login_demo(client: TestClient):
    """demo / demo123 登录返 200 + access_token."""
    resp = client.post("/api/v1/auth/login", json={
        "username": "demo",
        "password": "demo123",
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["token_type"] == "Bearer"
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["user"]["username"] == "demo"
    assert data["user"]["is_demo"] is True
    assert data["user"]["tenant_code"] == "demo"


def test_login_wrong_password(client: TestClient):
    """错误密码返 401, 不区分用户不存在."""
    resp = client.post("/api/v1/auth/login", json={
        "username": "demo",
        "password": "wrong_password",
    })
    assert resp.status_code == 401


def test_login_nonexistent_user(client: TestClient):
    """不存在的用户也返 401, 跟密码错同返回 (防爆破枚举)."""
    resp = client.post("/api/v1/auth/login", json={
        "username": "this_user_does_not_exist_xyz",
        "password": "any",
    })
    assert resp.status_code == 401


def test_register_and_login(client: TestClient):
    """注册 -> 登录 -> /me 完整流程.

    用 user_test_<uuid>@example.com 邮箱 (test.local 是保留 TLD, Pydantic 拒).
    """
    username = f"user_test_{uuid.uuid4().hex[:8]}"
    password = "Test1234!"
    email = f"{username}@example.com"

    # 注册
    resp = client.post("/api/v1/auth/register", json={
        "username": username,
        "password": password,
        "email": email,
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["user"]["username"] == username
    assert data["user"]["tenant_code"] == f"user_{username}"
    access_token = data["access_token"]

    # 用 access_token 调 /me
    resp = client.get("/api/v1/auth/me", headers={
        "Authorization": f"Bearer {access_token}",
    })
    assert resp.status_code == 200
    me = resp.json()["data"]
    assert me["user"]["username"] == username
    assert me["stats"]["building_count"] == 0  # 新租户没楼

    # 用密码登录
    resp = client.post("/api/v1/auth/login", json={
        "username": username,
        "password": password,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["user"]["username"] == username


def test_register_duplicate(client: TestClient):
    """重复注册同一用户名返 409."""
    username = f"user_dup_{uuid.uuid4().hex[:8]}"
    payload = {
        "username": username,
        "password": "Test1234!",
        "email": f"{username}@example.com",
    }
    resp1 = client.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201

    resp2 = client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code == 409


def test_refresh_token(client: TestClient):
    """refresh token 换新 access token."""
    resp = client.post("/api/v1/auth/login", json={
        "username": "demo",
        "password": "demo123",
    })
    refresh_token = resp.json()["data"]["refresh_token"]

    resp2 = client.post("/api/v1/auth/refresh", json={
        "refresh_token": refresh_token,
    })
    assert resp2.status_code == 200
    assert resp2.json()["data"]["access_token"]


def test_refresh_invalid_token(client: TestClient):
    """无效 refresh token 返 401."""
    resp = client.post("/api/v1/auth/refresh", json={
        "refresh_token": "invalid_token_string",
    })
    assert resp.status_code == 401


def test_me_without_token(client: TestClient):
    """无 token 调 /me 返 401."""
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
