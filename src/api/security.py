"""JWT 认证与角色权限（FastAPI 依赖注入）。

设计要点：
* 密码哈希用标准库 ``hashlib.pbkdf2_hmac``（12 万次迭代 + 随机盐），不引入
  passlib/bcrypt，减少依赖面；存储格式 ``pbkdf2_sha256$<salt>$<digest>``；
* Token 用 PyJWT HS256，载荷含 ``sub``（用户名）/ ``role`` / ``exp``；
* HTTP 侧走 ``Authorization: Bearer <token>``；WebSocket 不能自定义请求头，
  走 ``?token=`` 查询参数，在 ``accept()`` 之前校验；
* 角色三级：admin（全部）/ operator（读 + 运维写）/ viewer（只读），
  与 README「接口鉴权矩阵」一致。
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, WebSocket
from fastapi.security import OAuth2PasswordBearer

from src.config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    AUTH_SECRET,
    JWT_ALGORITHM,
    JWT_EXPIRE_MINUTES,
    ROLES,
)
from src.observability import get_logger

_PBKDF2_ITERATIONS = 120_000

if not AUTH_SECRET:
    # 未配置 AUTH_SECRET 时使用进程级临时密钥：功能可用，但重启后全部 token 失效。
    # 生产/演示环境务必在 .env 配置固定 AUTH_SECRET。
    AUTH_SECRET = os.urandom(32).hex()
    get_logger("api").warning(
        '{"action": "auth_secret_missing", "detail": "AUTH_SECRET 未配置，使用临时开发密钥；重启后 token 全部失效，请在 .env 配置固定值"}'
    )

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ---------------------------------------------------------------- 密码哈希

def hash_password(password: str, salt: bytes | None = None) -> str:
    """生成 pbkdf2_sha256 密码哈希；salt 随机生成，可传入以复现。"""

    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${salt.hex()}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间比对密码与存储哈希；格式不符一律返回 False。"""

    try:
        algorithm, salt_hex, expected = stored.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hash_password(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(candidate.split("$", 2)[2], expected)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- Token

def create_access_token(username: str, role: str, expires_minutes: int | None = None) -> str:
    """签发 JWT；默认有效期取 JWT_EXPIRE_MINUTES。"""

    minutes = expires_minutes if expires_minutes is not None else JWT_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "role": role, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, AUTH_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """解析并校验 JWT；过期/无效一律抛 ``jwt.InvalidTokenError``。"""

    return jwt.decode(token, AUTH_SECRET, algorithms=[JWT_ALGORITHM])


# ---------------------------------------------------------------- FastAPI 依赖

def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, token: str | None = Depends(oauth2_scheme)) -> dict:
    """从 Authorization 头解析当前登录用户（不存在/过期/无效均 401）。"""

    if not token:
        raise _unauthorized("未登录：请先登录获取访问令牌")
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as error:
        raise _unauthorized("登录已过期，请重新登录") from error
    except jwt.InvalidTokenError as error:
        raise _unauthorized("无效的登录凭证") from error
    username = str(payload.get("sub", ""))
    user = request.app.state.database.get_user_by_username(username)
    if user is None:
        raise _unauthorized("账号不存在或已被删除")
    return {"username": str(user["username"]), "role": str(user["role"]), "display_name": user.get("display_name")}


def require_role(*roles: str):
    """生成"必须拥有指定角色之一"的依赖工厂。"""

    allowed = set(roles)

    def dependency(user: Annotated[dict, Depends(get_current_user)]) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail=f"权限不足：需要 {' / '.join(sorted(allowed))} 角色")
        return user

    return dependency


CurrentUser = Annotated[dict, Depends(get_current_user)]
"""任何已登录用户（admin / operator / viewer）。"""
OperatorUp = Annotated[dict, Depends(require_role("admin", "operator"))]
"""运维及以上（admin / operator）：触发诊断、建工单、审批等写操作。"""
AdminOnly = Annotated[dict, Depends(require_role("admin"))]
"""仅管理员：开关自主循环、推送报告、推进仿真、账号管理。"""


async def authenticate_websocket(socket: WebSocket) -> dict | None:
    """WebSocket 鉴权：token 走 query 参数，失败直接 close(1008)。"""

    token = socket.query_params.get("token")
    if not token:
        await socket.close(code=1008, reason="missing token")
        return None
    try:
        payload = decode_access_token(token)
    except jwt.InvalidTokenError:
        await socket.close(code=1008, reason="invalid token")
        return None
    user = socket.app.state.database.get_user_by_username(str(payload.get("sub", "")))
    if user is None:
        await socket.close(code=1008, reason="unknown user")
        return None
    return {"username": str(user["username"]), "role": str(user["role"])}


def ensure_admin_user(database) -> None:
    """users 表为空时按 .env 凭据播种管理员（幂等，服务启动时调用）。"""

    if database.count("users") > 0:
        return
    password = ADMIN_PASSWORD or "admin123456"
    if not ADMIN_PASSWORD:
        get_logger("api").warning(
            '{"action": "admin_password_default", "detail": "ADMIN_PASSWORD 未配置，已播种默认口令 admin123456，请尽快在 .env 修改并重启"}'
        )
    database.create_user(username=ADMIN_USERNAME, password_hash=hash_password(password), role="admin", display_name="系统管理员")
    get_logger("api").info(
        '{"action": "admin_seeded", "username": "%s", "role": "admin"}' % ADMIN_USERNAME
    )


def role_options() -> tuple[str, ...]:
    """返回可用角色集合（供 API 文档与前端使用）。"""

    return ROLES
