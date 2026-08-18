"""
psycopg2 连接池 + 请求级连接管理。

设计取舍：
- 用 psycopg2.pool.ThreadedConnectionPool，最小 2 最大 10。FastAPI 默认
  线程池跑同步路由，多线程共用一个池没问题。后续如果切异步（async route）
  再考虑 psycopg3 async pool，但当前提示词明确要求 psycopg2。
- 每个请求从池里 checkout 一个连接，请求结束归还。用 FastAPI 的
  Depends + yield 模式实现。
- 每次 checkout 后先 SET search_path 到配置的 schema 列表，避免 SQL 里
  写死 schema 前缀（也避免 ingest.staging_reading 这种点号在 copy_from
  里被当表名一部分的坑，seed 脚本里踩过）。
- tenant_id 隔离：不在连接层做（连接是池化的，跨请求复用），而是在
  业务 SQL 里强制带 tenant_id 过滤。get_tenant_cursor 提供一个语义化
  入口，但隔离责任在调用方。后续认证模块会注入 current_tenant_id。
"""
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from loguru import logger

from app.core.config import settings


_pool: ThreadedConnectionPool | None = None


def init_pool() -> None:
    """初始化连接池。应用启动时调一次。"""
    global _pool
    if _pool is not None:
        return
    try:
        _pool = ThreadedConnectionPool(
            minconn=settings.db_pool_min,
            maxconn=settings.db_pool_max,
            dsn=settings.database_url,
        )
    except UnicodeDecodeError as e:
        # Windows 中文系统 PG 默认 lc_messages 是 'Chinese (Simplified)_China.936'
        # (GBK 编码), 连接握手阶段 server 主动发的 NoticeResponse 是 GBK 字节流。
        # psycopg2 的 C 扩展 _connect 用 UTF-8 解这些字节就炸, position 不是 dsn
        # 里的位置, 是 server notice 里的位置。0xd6 是 GBK 中文字符首字节
        # (如"数"= 0xca 0xfd, "服"= 0xb7 0xfe)。dsn 全 ASCII 不背锅。
        # 治本: 改 E:/PostgreSQL/15/data/postgresql.conf 的 lc_messages='C'
        # 然后 net stop/start postgresql-x64-15 重启 PG 服务。
        dsn = settings.database_url
        non_ascii = [(i, b) for i, b in enumerate(dsn.encode("utf-8")) if b > 127]
        logger.error(
            "数据库连接握手 UnicodeDecodeError。PG 服务 lc_messages 大概率是 GBK 中文 "
            "locale, 握手 notice 用 UTF-8 解码炸。"
            "治本: 改 postgresql.conf 的 lc_messages='C' 后重启 PG 服务。"
            "byte=0x{:02x} position={} dsn_len={} dsn_non_ascii={}",
            e.object[e.start] if e.object else 0,
            e.start,
            len(dsn),
            non_ascii,
        )
        raise
    except Exception as e:
        logger.error("数据库连接池初始化失败: {}", e)
        raise
    logger.info("数据库连接池就绪：min={} max={}", settings.db_pool_min, settings.db_pool_max)


def close_pool() -> None:
    """关闭连接池。应用退出时调。"""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("数据库连接池已关闭")


@contextmanager
def get_conn() -> Iterator[psycopg2.extensions.connection]:
    """
    请求级连接上下文管理器。从池里取连接，用完归还。

    用法：
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                ...

    异常时回滚，正常时由调用方决定 commit 时机（写操作要显式 conn.commit()）。
    """
    if _pool is None:
        raise RuntimeError("连接池未初始化，请先调 init_pool()")
    conn = _pool.getconn()
    try:
        # 每次取出都重新 SET search_path，因为连接被复用时
        # 上一个请求可能改过 session 级变量
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {settings.db_search_path}")
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def get_db() -> Iterator[psycopg2.extensions.connection]:
    """
    FastAPI 依赖注入入口。

    路由里用：
        @router.get("/...")
        def list_buildings(conn = Depends(get_db)):
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM building LIMIT 10")
                return cur.fetchall()
    """
    with get_conn() as conn:
        yield conn


def ping() -> bool:
    """轻量探活，给 /ready 端点用。SELECT 1 走一趟。"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        logger.warning("数据库探活失败：{}", e)
        return False
