"""
密码哈希 + JWT 签发/校验。

哈希用 bcrypt 库直接调用, 不走 passlib. 原因: passlib 1.7.4 跟 bcrypt 4.x
不兼容 (bcrypt 4.x 移除了 __about__ 模块, passlib 加载 backend 时抛
AttributeError). bcrypt 库的 hashpw / checkpw 足够简单, 不需要 passlib
的 schemes 抽象. 旧 password_hash ($2b$12$...) 兼容, 不用迁移.

JWT 用 python-jose,HS256 对称加密(单机部署够用,不用搞 RSA 非对称)。
payload 里带 token_type 区分 access / refresh,/refresh 接口校验类型。
不存库,签出去就管不了,7 天内 refresh 被偷只能等过期--一期可接受,
后续要吊销能力再加 core.user 的 refresh_token_hash 字段。
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def hash_password(plain: str) -> str:
    """bcrypt 哈希, 返 $2b$12$... 格式字符串存库. 每次哈希盐值不同."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """比对明文密码跟库里的 bcrypt hash. hash 格式不对 (空/非 $2b$ 开头) 直接返 False."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(
    subject: str,
    tenant_id: str,
    token_type: str,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,        # user_id
        "tid": tenant_id,      # tenant_id,业务查询直接从 token 拿,不用再查库
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def create_access_token(user_id: str, tenant_id: str, username: str) -> str:
    """access token,默认 24h。username 放进去是为了日志里能看出谁在请求,不查库。"""
    return _create_token(
        user_id, tenant_id, TOKEN_TYPE_ACCESS,
        timedelta(hours=settings.jwt_expire_hours),
        extra={"u": username},
    )


def create_refresh_token(user_id: str, tenant_id: str) -> str:
    """refresh token,默认 7d。比 access 少放字段,减小 token 体积。"""
    return _create_token(
        user_id, tenant_id, TOKEN_TYPE_REFRESH,
        timedelta(days=settings.jwt_refresh_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    """解码并校验签名 + 过期。失败抛 JWTError,调用方决定怎么转 HTTP 错误。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])


class TokenInvalid(Exception):
    """token 无效/过期/类型不对。deps 里转成 401。"""


def require_token_type(token: str, expected_type: str) -> dict[str, Any]:
    """解码并校验类型。/refresh 用这,确保拿 refresh 换 access,而不是拿 access 自我续期。"""
    try:
        payload = decode_token(token)
    except JWTError as e:
        raise TokenInvalid(f"token 解码失败: {e}") from e
    if payload.get("type") != expected_type:
        raise TokenInvalid(f"token 类型不符,期望 {expected_type}")
    if "sub" not in payload or "tid" not in payload:
        raise TokenInvalid("token 缺少必要字段")
    return payload
