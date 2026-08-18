"""
请求级依赖注入:认证 + 租户隔离 + demo 写保护。

三个依赖按需挂:
- get_current_user:校验登录态,返回 CurrentUser。所有需登录的接口都挂
- get_current_tenant_id:轻量版,只要 tenant_id 的接口用,内部包了 get_current_user
- require_write_access:写操作专用,demo 用户调到直接 403

CurrentUser 把数据库查出来的字段缓存一份,后续业务代码不用再查库。
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from loguru import logger

from app.core.security import TOKEN_TYPE_ACCESS, TokenInvalid, decode_token
from app.db.session import get_db


@dataclass
class CurrentUser:
    user_id: str
    tenant_id: str
    username: str
    is_demo: bool


def _get_token_from_header(authorization: str | None) -> str:
    """从 Authorization: Bearer xxx 头里取 token。"""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization 头",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization 头格式错误,应为 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1]


def get_current_user(
    authorization: str | None = Header(None),
    conn=Depends(get_db),
) -> CurrentUser:
    """
    校验 access token,返回当前用户。

    token 里带了 user_id/tenant_id/username,但 is_demo 不在 token 里
    (避免改 demo 状态后老 token 还认),需要查一次库确认。
    库里查不到用户(user_id 不存在或被删)直接 401,不让过期 token 继续用。
    """
    token = _get_token_from_header(authorization)
    try:
        payload = decode_token(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"token 无效或已过期: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 类型错误,请使用 access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    tenant_id = payload.get("tid")
    username = payload.get("u")
    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 缺少必要字段",
            headers={"WWW-Authenticate": "Bearer"},
        )

    with conn.cursor() as cur:
        cur.execute(
            'SELECT id, tenant_id, username, is_demo, is_active FROM core."user" WHERE id = %s',
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已删除",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not row[4]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用",
        )

    return CurrentUser(
        user_id=str(row[0]),
        tenant_id=str(row[1]),
        username=row[2],
        is_demo=row[3],
    )


def get_current_tenant_id(user: CurrentUser = Depends(get_current_user)) -> str:
    """业务查询只需 tenant_id 的接口用这个,省得引整个 CurrentUser。"""
    return user.tenant_id


def require_write_access(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """
    写操作专用依赖。demo 用户调到直接 403。

    挂法:
        @router.post("/uploads")
        def upload(..., user: CurrentUser = Depends(require_write_access)):
            ...
    """
    if user.is_demo:
        logger.info("demo 用户 {} 尝试写操作,已拦截", user.username)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="demo 账号为只读,无法执行写操作",
        )
    return user
