"""
demo 体验账号自动创建。

启动时调一次 ensure_demo_user()。demo 用户绑定到 BDG2 seed 时建的 demo
租户(tenant_code='demo'),只读访问那 6 栋楼数据。

幂等:已存在就不动,密码也不覆盖——避免每次重启把管理员手动改过的密码
重置回去。如果确实要重置,删 core."user" 里 username='demo' 那行再启动。
"""
from loguru import logger

from app.core.security import hash_password
from app.db.session import get_conn


DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo123"
DEMO_TENANT_CODE = "demo"
DEMO_TENANT_NAME = "BDG2 演示数据"


def ensure_demo_user() -> None:
    """启动时调。demo 用户不存在则创建,存在则跳过。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 先确保 demo 租户存在(如果用户还没跑过 BDG2 seed,这里兜底建一个空租户)
            cur.execute(
                """
                INSERT INTO core.tenant (tenant_code, tenant_name)
                VALUES (%s, %s)
                ON CONFLICT (tenant_code) DO NOTHING
                RETURNING id
                """,
                (DEMO_TENANT_CODE, DEMO_TENANT_NAME),
            )
            tenant_row = cur.fetchone()
            if tenant_row is not None:
                tenant_id = tenant_row[0]
                logger.info("demo 租户不存在,已创建 tenant_id={}", tenant_id)
            else:
                cur.execute(
                    "SELECT id FROM core.tenant WHERE tenant_code = %s",
                    (DEMO_TENANT_CODE,),
                )
                tenant_id = cur.fetchone()[0]
                logger.info("demo 租户已存在 tenant_id={}", tenant_id)

            # 检查 demo 用户是否已存在
            cur.execute(
                'SELECT id FROM core."user" WHERE username = %s',
                (DEMO_USERNAME,),
            )
            if cur.fetchone() is not None:
                logger.info("demo 用户已存在,跳过创建")
                conn.rollback()
                return

            cur.execute(
                """
                INSERT INTO core."user"
                    (tenant_id, username, password_hash, display_name, is_demo)
                VALUES (%s, %s, %s, %s, true)
                """,
                (tenant_id, DEMO_USERNAME, hash_password(DEMO_PASSWORD), "BDG2 体验账号"),
            )
            conn.commit()
            logger.info("demo 用户已创建(只读),用户名={} 密码={}", DEMO_USERNAME, DEMO_PASSWORD)
