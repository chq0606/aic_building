"""
园区列表服务 (Step 14)。

为什么单独建一个 service 而不是塞进 query_service:
  query_service 的 6 个查询都是"给定 site_id 后查数据"，site 列表是"先列出
  租户有哪些 site"。语义层级不同, 放一起会让 query_service 文件越来越杂。
  后续 Step 16 数据接入页可能再加"创建 site", Step 17 系统设置加"修改 site
  经纬度", 都放这里聚集。

list_sites 是 step14 顶栏站点切换器的数据源:
  前端 DefaultLayout onMounted 调一次, 拿到 site_id 列表后填进下拉框。
  context.setSite(id) 触发 Park.vue 重新加载 scene。

building_count 用 LEFT JOIN + COUNT:
  site 即使没 building 也返回 (count=0), 前端顶栏不希望"突然某个 site
  消失"。LEFT JOIN 比 子查询快 (PostgreSQL 优化器对 LEFT JOIN + COUNT(*)
  有专门优化)。

latitude/longitude:
  core.site 表有这两个字段, 但 seed_bdg2.py 没填, demo 数据返 NULL。
  前端兜底不依赖 (用本地笛卡尔坐标系), 这里直接返原值。
  后续如果客户接入真实 site (Step 16), 可以填真实经纬度让前端切到
  CesiumJS 地球模式 (本 step 不实现)。

tenant_id 隔离:
  WHERE s.tenant_id = %s, 不依赖连接层。其他 service 一致。
"""
from typing import Any

from loguru import logger
import psycopg2
import uuid

from app.db.session import get_conn


def create_site(
    tenant_id: str,
    site_name: str,
    site_code: str | None = None,
    timezone: str = "UTC",
) -> dict[str, Any]:
    """新建园区。site_code 可空, 空则自动生成 site_<8位hex>。

    返 {site_id, site_code, site_name}。site_code 跟租户内已有园区撞了抛
    ValueError (路由层转 400)。source_dataset 标 'user_upload' 区分 demo seed。
    """
    code = (site_code or "").strip() or f"site_{uuid.uuid4().hex[:8]}"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO core.site (tenant_id, site_code, site_name, timezone, source_dataset)
                    VALUES (%s::uuid, %s, %s, %s, 'user_upload')
                    RETURNING id, site_code, site_name
                """, (tenant_id, code, site_name, timezone))
                row = cur.fetchone()
            conn.commit()
    except psycopg2.errors.UniqueViolation:
        raise ValueError(f"园区编码已存在: {code}")

    logger.info("create_site tenant={} code={}", tenant_id[:8], code)
    return {"site_id": str(row[0]), "site_code": row[1], "site_name": row[2]}


def list_sites(tenant_id: str) -> list[dict[str, Any]]:
    """列出当前租户所有 site。

    返回 [{site_id, site_code, site_name, latitude, longitude, building_count}]。
    顺序按 site_code 升序, 保证前端下拉框顺序稳定 (否则每次刷新顺序变, 用户体验差)。

    demo 租户只有 Bobcat 一个 site, building_count=6。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    s.id,
                    s.site_code,
                    s.site_name,
                    s.latitude,
                    s.longitude,
                    COUNT(b.id) AS building_count
                FROM core.site s
                LEFT JOIN core.building b
                    ON b.site_id = s.id
                   AND b.tenant_id = s.tenant_id
                WHERE s.tenant_id = %s::uuid
                GROUP BY s.id, s.site_code, s.site_name, s.latitude, s.longitude
                ORDER BY s.site_code
            """, (tenant_id,))
            rows = cur.fetchall()

    sites = [
        {
            "site_id": str(row[0]),
            "site_code": row[1],
            "site_name": row[2],
            "latitude": float(row[3]) if row[3] is not None else None,
            "longitude": float(row[4]) if row[4] is not None else None,
            "building_count": row[5],
        }
        for row in rows
    ]
    logger.info(
        "list_sites tenant={} count={}",
        tenant_id[:8], len(sites),
    )
    return sites
