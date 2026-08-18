"""
认证路由:注册 / 登录 / refresh / me / change-password。

注册流程的事务边界:建 tenant + 建 user 必须原子,任何一个失败都回滚,
避免出现"有 tenant 没 user"的脏数据。用 with conn + 显式 commit。

username 全局唯一靠 core."user".username 的 UNIQUE 约束兜底,
并发注册同名时第二个会拿到 23505 (unique_violation),转成 409。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from psycopg2.errors import UniqueViolation

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.response import success, error
from app.core.security import (
    TokenInvalid,
    create_access_token,
    create_refresh_token,
    hash_password,
    require_token_type,
    verify_password,
)
from app.db.session import get_db
from app.models.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
)


router = APIRouter(prefix="/auth", tags=["auth"])


def _build_token_response(conn, user_row) -> dict:
    """组装登录/注册成功后的返回体。user_row 是查出来的用户记录。"""
    user_id, tenant_id, username, display_name, email, is_demo, tenant_code, tenant_name = user_row
    access = create_access_token(user_id, tenant_id, username)
    refresh = create_refresh_token(user_id, tenant_id)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": settings.jwt_expire_hours * 3600,
        "user": {
            "id": user_id,
            "tenant_id": tenant_id,
            "username": username,
            "display_name": display_name,
            "email": email,
            "is_demo": is_demo,
            "tenant_code": tenant_code,
            "tenant_name": tenant_name,
        },
    }


@router.post("/register", status_code=201)
def register(req: RegisterRequest, conn=Depends(get_db)):
    """
    注册:用户名 + 密码 + 邮箱(可选)。
    一人一租户:自动建 tenant(tenant_code = user_<username>),再建 user 绑过去。
    """
    username = req.username
    tenant_code = f"user_{username}"
    logger.info("注册请求: username={}", username)

    with conn.cursor() as cur:
        # 先查 username 是否已存在,给一个友好的 409 而不是 500
        cur.execute('SELECT 1 FROM core."user" WHERE username = %s', (username,))
        if cur.fetchone() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error(message="用户名已存在", code=409),
            )

        try:
            cur.execute(
                "INSERT INTO core.tenant (tenant_code, tenant_name) VALUES (%s, %s) RETURNING id",
                (tenant_code, username),
            )
            tenant_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO core."user"
                    (tenant_id, username, password_hash, display_name, email)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, tenant_id, username, display_name, email, is_demo
                """,
                (tenant_id, username, hash_password(req.password),
                 username, str(req.email) if req.email else None),
            )
            u = cur.fetchone()
            conn.commit()
        except UniqueViolation as e:
            conn.rollback()
            # 并发场景:刚 SELECT 没冲突,INSERT 时另一个请求先建了同名
            logger.warning("注册并发冲突: username={}", username)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error(message="用户名已存在", code=409),
            ) from e
        except Exception:
            conn.rollback()
            raise

    payload = _build_token_response(conn, (
        str(u[0]), str(u[1]), u[2], u[3], u[4], u[5],
        tenant_code, username,
    ))
    logger.info("注册成功: username={} tenant_id={}", username, tenant_id)
    return success(data=payload, message="注册成功")


@router.post("/login")
def login(req: LoginRequest, conn=Depends(get_db)):
    """用户名 + 密码 → JWT。错误统一返回 401,不区分"用户不存在"和"密码错",防爆破枚举。"""
    logger.info("登录请求: username={}", req.username)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.tenant_id, u.username, u.display_name, u.email,
                   u.is_demo, u.is_active, u.password_hash,
                   t.tenant_code, t.tenant_name
            FROM core."user" u
            JOIN core.tenant t ON t.id = u.tenant_id
            WHERE u.username = %s
            """,
            (req.username,),
        )
        row = cur.fetchone()

    if row is None or not verify_password(req.password, row[7]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error(message="用户名或密码错误", code=401),
        )
    if not row[6]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error(message="账号已被禁用", code=403),
        )

    with conn.cursor() as cur:
        cur.execute('UPDATE core."user" SET last_login_at = now() WHERE id = %s', (row[0],))
        conn.commit()

    payload = _build_token_response(conn, (
        str(row[0]), str(row[1]), row[2], row[3], row[4], row[5],
        row[8], row[9],
    ))
    logger.info("登录成功: username={}", req.username)
    return success(data=payload, message="登录成功")


@router.post("/refresh")
def refresh(req: RefreshRequest, conn=Depends(get_db)):
    """
    用 refresh token 换新的 access + refresh。
    refresh 不存库,只校验签名 + 类型 + 用户是否还存在/启用。
    """
    try:
        payload = require_token_type(req.refresh_token, "refresh")
    except TokenInvalid as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error(message=str(e), code=401),
        )

    user_id = payload["sub"]
    tenant_id = payload["tid"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.tenant_id, u.username, u.display_name, u.email,
                   u.is_demo, u.is_active, t.tenant_code, t.tenant_name
            FROM core."user" u
            JOIN core.tenant t ON t.id = u.tenant_id
            WHERE u.id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()

    if row is None or not row[6]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error(message="用户不存在或已禁用", code=401),
        )

    payload_resp = _build_token_response(conn, (
        str(row[0]), str(row[1]), row[2], row[3], row[4], row[5],
        row[7], row[8],
    ))
    return success(data=payload_resp, message="刷新成功")


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user), conn=Depends(get_db)):
    """当前用户信息 + 租户信息 + 一点统计(楼数、读数)。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.tenant_id, u.username, u.display_name, u.email,
                   u.is_demo, t.tenant_code, t.tenant_name
            FROM core."user" u
            JOIN core.tenant t ON t.id = u.tenant_id
            WHERE u.id = %s
            """,
            (user.user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=error(message="用户不存在", code=404))

        # 统计这个租户的楼数和读数,前端用来在顶栏显示
        cur.execute(
            "SELECT COUNT(*) FROM core.building WHERE tenant_id = %s",
            (user.tenant_id,),
        )
        building_count = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM fact.point_reading WHERE tenant_id = %s",
            (user.tenant_id,),
        )
        reading_count = cur.fetchone()[0]

    return success(data={
        "user": {
            "id": str(row[0]),
            "tenant_id": str(row[1]),
            "username": row[2],
            "display_name": row[3],
            "email": row[4],
            "is_demo": row[5],
            "tenant_code": row[6],
            "tenant_name": row[7],
        },
        "stats": {
            "building_count": building_count,
            "reading_count": reading_count,
        },
    })


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """
    修改自己密码。要求带旧密码。
    demo 用户也能改——但改了就破坏 demo 体验账号,这里直接拦掉。
    """
    if user.is_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error(message="demo 账号不允许修改密码", code=403),
        )

    with conn.cursor() as cur:
        cur.execute(
            'SELECT password_hash FROM core."user" WHERE id = %s',
            (user.user_id,),
        )
        row = cur.fetchone()
        if row is None or not verify_password(req.old_password, row[0]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error(message="旧密码错误", code=400),
            )
        if req.old_password == req.new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error(message="新密码不能与旧密码相同", code=400),
            )

        cur.execute(
            'UPDATE core."user" SET password_hash = %s WHERE id = %s',
            (hash_password(req.new_password), user.user_id),
        )
        conn.commit()

    logger.info("用户 {} 修改了密码", user.username)
    return success(message="密码修改成功,请重新登录")
