"""
pytest 公共 fixture.

用法:
    cd code/backend
    pytest tests/ -v

设计要点:
- session 级 db_pool: init_pool 一次, 不调 close_pool (让 TestClient lifespan 管 close)
- session 级 demo_user / demo_building_id / demo_site_id: 缓存 demo 数据的 ID,
  避免 function 级 fixture 在 TestClient close_pool 后取不到连接
- 函数级 client: 每个 test 起一个 TestClient, lifespan 内 init_pool (no-op) + close_pool
- auth_demo / auth_normal: 函数级, 注入 CurrentUser 到 dependency_overrides, 用完清理
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 让 from app import ... 能跑 (tests/ 在 code/backend/ 下, parent 就是 backend/)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.deps import CurrentUser, get_current_user  # noqa: E402
from app.db.session import get_conn, init_pool  # noqa: E402
from app.main import app  # noqa: E402

# Windows 控制台默认 GBK, pytest 抓 print 输出会乱码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# session 级: 启动连接池 + 缓存 demo 数据 ID
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def db_pool():
    """session 级连接池, 整个 pytest 跑一遍只 init 一次.

    不调 close_pool, 让 OS 进程退出时回收. 这样 TestClient 的 lifespan close_pool
    不会跟 session scope 冲突.
    """
    init_pool()
    yield


@pytest.fixture(scope="session")
def _demo_data(db_pool):
    """session 级缓存 demo 用户 / site / building 的 ID.

    一次性查出来缓存, 避免每个 function-scope test 都查库 (TestClient 退出会关 pool,
    下一个 test 进来 pool 已关, 这里查会失败).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # demo 用户
            cur.execute(
                'SELECT id, tenant_id, username, is_demo FROM core."user" WHERE username = %s',
                ("demo",),
            )
            row = cur.fetchone()
            if row is None:
                pytest.exit(
                    "demo 用户不存在, 请先跑 backend 启动 (会自动 ensure_demo_user)",
                    returncode=2,
                )
            demo_user = CurrentUser(
                user_id=str(row[0]),
                tenant_id=str(row[1]),
                username=row[2],
                is_demo=row[3],
            )

            # demo site (Bobcat)
            cur.execute(
                "SELECT s.id FROM core.site s JOIN core.tenant t ON t.id = s.tenant_id "
                "WHERE t.tenant_code = 'demo' AND s.site_code = 'Bobcat'"
            )
            site_row = cur.fetchone()
            if site_row is None:
                pytest.exit("demo site 不存在, 请先跑 seed_bdg2.py", returncode=2)
            demo_site_id = str(site_row[0])

            # demo building (取第一个)
            cur.execute(
                "SELECT b.id FROM core.building b "
                "JOIN core.tenant t ON t.id = b.tenant_id "
                "WHERE t.tenant_code = 'demo' "
                "ORDER BY b.building_code LIMIT 1"
            )
            b_row = cur.fetchone()
            if b_row is None:
                pytest.exit("demo building 不存在, 请先跑 seed_bdg2.py", returncode=2)
            demo_building_id = str(b_row[0])

    return {
        "demo_user": demo_user,
        "demo_site_id": demo_site_id,
        "demo_building_id": demo_building_id,
    }


@pytest.fixture(scope="session")
def demo_user(_demo_data) -> CurrentUser:
    return _demo_data["demo_user"]


@pytest.fixture(scope="session")
def demo_site_id(_demo_data) -> str:
    return _demo_data["demo_site_id"]


@pytest.fixture(scope="session")
def demo_building_id(_demo_data) -> str:
    return _demo_data["demo_building_id"]


# ---------------------------------------------------------------------------
# 函数级: TestClient + auth stub
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db_pool):
    """每个 test 起一个 TestClient, 自动走 lifespan (init_pool + close_pool).

    第一次 test: db_pool 已 init, lifespan init_pool 是 no-op.
    后续 test: 上一个 TestClient close_pool 后 _pool = None, lifespan init_pool 重建.
    """
    with TestClient(app) as c:
        yield c


@pytest.fixture
def normal_user(demo_user) -> CurrentUser:
    """伪装成非 demo 用户 (复用 demo 的 user_id/tenant_id, 但 is_demo=False).
    用于写操作测试 (require_write_access 拦截 is_demo=True).
    """
    return CurrentUser(
        user_id=demo_user.user_id,
        tenant_id=demo_user.tenant_id,
        username=demo_user.username,
        is_demo=False,
    )


@pytest.fixture
def auth_demo(client, demo_user):
    """注入 demo 用户到 get_current_user 依赖."""
    def _stub():
        return demo_user
    app.dependency_overrides[get_current_user] = _stub
    yield demo_user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def auth_normal(client, normal_user):
    """注入非 demo 用户 (绕过 require_write_access)."""
    def _stub():
        return normal_user
    app.dependency_overrides[get_current_user] = _stub
    yield normal_user
    app.dependency_overrides.pop(get_current_user, None)
