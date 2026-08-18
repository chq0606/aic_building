"""
staging -> fact 的增量 upsert。

step 04 已经把数据 COPY 到 ingest.staging_reading（text 字段，因为格式未校验）。
本模块负责把 staging 表的数据 merge 到 fact.point_reading：
- 通过 point_code JOIN core.point 拿 point_id
- ts_text::timestamptz 转 timestamp
- value_text::double precision 转数值
- ON CONFLICT (point_id, ts) DO UPDATE 实现增量 upsert，同 point + ts 重复数据覆盖

source_batch_id 写到 fact.point_reading，rollback 时按这个字段定位本批数据。
"""
from loguru import logger

from app.db.session import get_conn


def upsert_staging_to_fact(batch_id: str, tenant_id: str) -> int:
    """
    把 staging_reading 里 batch_id 对应的数据 merge 到 fact.point_reading。

    返回受影响行数（INSERT 新行 + UPDATE 覆盖行）。

    用 INSERT ... ON CONFLICT DO UPDATE 在一条 SQL 里完成 upsert。
    PG 在分区父表上支持 ON CONFLICT (point_id, ts)（主键在父表）。
    source_batch_id 写到 fact.point_reading，rollback 时按这个字段定位本批数据。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH affected AS (
                    SELECT
                        p.tenant_id, p.site_id, p.building_id, p.id AS point_id,
                        sr.ts_text::timestamptz AS ts,
                        sr.value_text::double precision AS value_num,
                        COALESCE(NULLIF(sr.quality_text, ''), 'GOOD') AS quality_code
                    FROM ingest.staging_reading sr
                    JOIN core.point p
                      ON p.tenant_id = %s AND p.point_code = sr.point_code
                    WHERE sr.batch_id = %s
                )
                INSERT INTO fact.point_reading
                    (tenant_id, site_id, building_id, point_id, ts,
                     value_num, quality_code, source_batch_id)
                SELECT
                    tenant_id, site_id, building_id, point_id, ts,
                    value_num, quality_code, %s
                FROM affected
                ON CONFLICT (point_id, ts) DO UPDATE SET
                    value_num       = EXCLUDED.value_num,
                    quality_code    = EXCLUDED.quality_code,
                    source_batch_id = EXCLUDED.source_batch_id,
                    updated_at      = now()
            """, (tenant_id, batch_id, batch_id))
            affected = cur.rowcount
        conn.commit()

    logger.info("upsert 完成: batch={} affected={}", batch_id, affected)
    return affected


def get_merge_stats(batch_id: str, tenant_id: str) -> dict:
    """查 merge 后的统计：受影响 building / point / 日期范围。给 aggregate_service 用。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(DISTINCT p.building_id) AS building_count,
                    COUNT(DISTINCT p.id) AS point_count,
                    MIN(pr.ts)::date AS min_date,
                    MAX(pr.ts)::date AS max_date,
                    COUNT(*) AS row_count
                FROM fact.point_reading pr
                JOIN core.point p ON p.id = pr.point_id
                WHERE pr.source_batch_id = %s AND pr.tenant_id = %s
            """, (batch_id, tenant_id))
            r = cur.fetchone()
    return {
        "building_count": r[0],
        "point_count": r[1],
        "min_date": r[2],
        "max_date": r[3],
        "row_count": r[4],
    }


def delete_fact_by_batch(batch_id: str, tenant_id: str) -> int:
    """rollback 用：按 source_batch_id 删 fact.point_reading。返回删除行数。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM fact.point_reading
                WHERE source_batch_id = %s AND tenant_id = %s
            """, (batch_id, tenant_id))
            deleted = cur.rowcount
        conn.commit()
    logger.info("rollback 删 fact.point_reading: batch={} rows={}", batch_id, deleted)
    return deleted
