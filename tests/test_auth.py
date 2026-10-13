"""认证与权限测试：密码哈希 / JWT / 用户表 / 角色依赖。

全部跑在内存 SQLite（conftest 的 memory_db fixture）上，不触碰生产库；
不启动 FastAPI  lifespan，只测确定性逻辑与依赖工厂的行为。
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException

from src.api import security
from src.database.connection import Database


# ---------------------------------------------------------------- 密码哈希

def test_password_hash_roundtrip():
    stored = security.hash_password("S3cret!pass")
    assert stored.startswith("pbkdf2_sha256$")
    assert security.verify_password("S3cret!pass", stored)


def test_password_hash_rejects_wrong_password():
    stored = security.hash_password("S3cret!pass")
    assert not security.verify_password("wrong-pass", stored)


def test_password_hash_rejects_malformed_stored_value():
    assert not security.verify_password("whatever", "not-a-hash")
    assert not security.verify_password("whatever", "md5$aa$bb")


def test_password_hash_is_salted():
    first = security.hash_password("same-password")
    second = security.hash_password("same-password")
    assert first != second  # 随机盐：相同口令哈希不同


# ---------------------------------------------------------------- JWT

def test_token_roundtrip_and_payload():
    token = security.create_access_token("alice", "operator", expires_minutes=30)
    payload = security.decode_access_token(token)
    assert payload["sub"] == "alice"
    assert payload["role"] == "operator"
    assert payload["exp"] > time.time()


def test_expired_token_is_rejected():
    token = security.create_access_token("alice", "admin", expires_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(token)


def test_token_with_tampered_signature_is_rejected():
    token = security.create_access_token("alice", "admin")
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(tampered)


# ---------------------------------------------------------------- 用户表

def test_user_crud_on_memory_db(memory_db: Database):
    user_id = memory_db.create_user(username="ops1", password_hash=security.hash_password("pw123456"), role="operator", display_name="运维小李")
    assert user_id.startswith("U-")

    fetched = memory_db.get_user_by_username("ops1")
    assert fetched is not None
    assert fetched["role"] == "operator"
    assert security.verify_password("pw123456", str(fetched["password_hash"]))

    listed = memory_db.list_users()
    assert len(listed) == 1
    assert "password_hash" not in listed[0]  # 列表不泄露哈希

    assert memory_db.update_user_role("ops1", "viewer")
    assert memory_db.get_user_by_username("ops1")["role"] == "viewer"
    assert memory_db.get_user_by_username("ghost") is None


def test_duplicate_username_is_rejected(memory_db: Database):
    memory_db.create_user(username="dup", password_hash=security.hash_password("pw123456"), role="viewer")
    with pytest.raises(Exception):  # sqlite3.IntegrityError：用户名唯一约束
        memory_db.create_user(username="dup", password_hash=security.hash_password("pw123456"), role="viewer")


# ---------------------------------------------------------------- 角色依赖

def test_require_role_allows_matching_role():
    dependency = security.require_role("admin", "operator")
    user = {"username": "boss", "role": "admin"}
    assert dependency(user) == user


def test_require_role_forbids_viewer_on_write_endpoints():
    dependency = security.require_role("admin", "operator")
    with pytest.raises(HTTPException) as excinfo:
        dependency({"username": "guest", "role": "viewer"})
    assert excinfo.value.status_code == 403


# ---------------------------------------------------------------- 管理员播种

def test_ensure_admin_user_is_idempotent(memory_db: Database, monkeypatch):
    monkeypatch.setattr(security, "ADMIN_PASSWORD", "seed-pass-123")
    security.ensure_admin_user(memory_db)
    security.ensure_admin_user(memory_db)  # 第二次调用不重复播种
    users = memory_db.list_users()
    assert len(users) == 1
    assert users[0]["username"] == security.ADMIN_USERNAME
    assert users[0]["role"] == "admin"
    stored = memory_db.get_user_by_username(security.ADMIN_USERNAME)
    assert security.verify_password("seed-pass-123", str(stored["password_hash"]))
