"""
数据质量指标计算。

按提示词 6 个指标：
  completeness               完整度 = 实际读数 / 应有读数（按 1 小时间隔）
  duplicate_rate             重复率 = staging 重复行 / staging 总数
  invalid_value_rate         无效值率 = fact 里负数或超限值 / 总数
  missing_gap_count          缺测时段数 = 相邻读数间隔 > 1.5 小时的次数
  timezone_parse_error_count 时区解析错误 = staging 里 ts_text 无法转 timestamptz 的行数
  unit_normalization_count   单位归一化次数 = 0（step 04 没记录这个，预留）

两个视角：
  building 视角：查 fact.point_reading WHERE building_id=X AND ts BETWEEN start AND end
                 completeness/invalid_value_rate/missing_gap_count 有意义
                 duplicate_rate/timezone_parse_error_count 返 N/A（fact 已去重）
  batch 视角：   查 staging_reading + fact.point_reading WHERE source_batch_id=X
                 duplicate_rate/timezone_parse_error_count/invalid_value_rate 有意义
                 completeness/missing_gap_count 返 N/A（按 batch 看时间跨度没意义）

返回结构统一 {metric, value, threshold, status, details}：
  status = ok / warning / critical，按阈值判定
  details 给出问题时段和问题点列表（前 20 条，避免响应过大）
"""
from dataclasses import dataclass, asdict
from typing import Any

from loguru import logger

from app.db.session import get_conn


# 阈值定义。每条 (ok_threshold, warning_threshold, comparison) 含义：
# - "ge" (>=): value >= ok_threshold -> ok; >= warning_threshold -> warning; 否则 critical
# - "le" (<=): value <= ok_threshold -> ok; <= warning_threshold -> warning; 否则 critical
THRESHOLDS: dict[str, tuple[float, float, str]] = {
    "completeness":               (0.95, 0.80, "ge"),
    "duplicate_rate":             (0.01, 0.05, "le"),
    "invalid_value_rate":         (0.001, 0.01, "le"),
    "missing_gap_count":          (0, 3, "le"),
    "timezone_parse_error_count": (0, 5, "le"),
    "unit_normalization_count":   (0, 5, "le"),
}

# 无效值判定：负数或超 1e6（保守阈值，BDG2 楼能耗单小时不会过百万 kWh）
INVALID_VALUE_THRESHOLD = 1e6


@dataclass
class MetricResult:
    """单个指标结果。前端拿这个结构渲染成卡片或表格行。"""
    metric: str
    value: float | int | None        # None 表示该视角下不适用（N/A）
    threshold: dict                 # {ok, warning, comparison}
    status: str                     # ok / warning / critical / n/a
    details: list[dict]             # 问题点列表，前 20 条

    def to_dict(self) -> dict:
        return asdict(self)


def _judge_status(metric: str, value: float | int | None) -> str:
    """按阈值判定 status。value=None 时返回 n/a。"""
    if value is None:
        return "n/a"
    ok_th, warn_th, cmp = THRESHOLDS[metric]
    if cmp == "ge":
        if value >= ok_th:
            return "ok"
        if value >= warn_th:
            return "warning"
        return "critical"
    else:  # le
        if value <= ok_th:
            return "ok"
        if value <= warn_th:
            return "warning"
        return "critical"


def _na_metric(metric: str, reason: str = "该视角下不适用") -> MetricResult:
    """构造 N/A 指标。building 视角下 duplicate_rate 等 fact 表查不到的指标用这个。"""
    ok_th, warn_th, cmp = THRESHOLDS[metric]
    return MetricResult(
        metric=metric,
        value=None,
        threshold={"ok": ok_th, "warning": warn_th, "comparison": cmp},
        status="n/a",
        details=[{"reason": reason}],
    )


# ── building 视角指标 ────────────────────────────────────────

def _building_completeness(cur, tenant_id: str, building_id: str, start_ts, end_ts) -> MetricResult:
    """
    完整度 = 实际读数 / 应有读数。
    应有读数 = (end - start) / 1 小时 * 有效 point 数
    实际读数 = COUNT(fact.point_reading WHERE building_id=X AND ts BETWEEN start AND end)

    有效 point 指时间范围内至少有 1 条读数的 point。
    建表时 core.point 会预挂空 point（比如某种 energy_type 还没接入数据），
    把它们算进 expected 会把分母撑大、把 completeness 拉低，但这种"还没接入"
    不属于数据质量问题，应该剔除。

    注意 point.sample_interval_sec 不一定准（建表时手动填），一期按 1 小时算。
    """
    cur.execute("""
        SELECT COUNT(DISTINCT p.id), COUNT(pr.ts)
        FROM core.point p
        LEFT JOIN fact.point_reading pr
          ON pr.point_id = p.id AND pr.ts >= %s AND pr.ts < %s
        WHERE p.tenant_id = %s AND p.building_id = %s
          AND EXISTS (
              SELECT 1 FROM fact.point_reading pr2
              WHERE pr2.point_id = p.id AND pr2.ts >= %s AND pr2.ts < %s
          )
    """, (start_ts, end_ts, tenant_id, building_id, start_ts, end_ts))
    point_count, reading_count = cur.fetchone()
    if point_count == 0:
        return _na_metric("completeness", "该 building 在时间范围内无 point 数据")

    # 应有读数：时间跨度小时数 * point 数。end - start 至少 1 小时
    cur.execute("SELECT EXTRACT(EPOCH FROM (%s::timestamptz - %s::timestamptz)) / 3600", (end_ts, start_ts))
    hours = float(cur.fetchone()[0])
    expected = max(hours, 1.0) * point_count
    completeness = reading_count / expected if expected > 0 else 0.0

    return MetricResult(
        metric="completeness",
        value=round(completeness, 4),
        threshold={"ok": 0.95, "warning": 0.80, "comparison": "ge"},
        status=_judge_status("completeness", completeness),
        details=[
            {"point_count": point_count, "reading_count": reading_count, "expected": int(expected)},
        ],
    )


def _building_invalid_value_rate(cur, tenant_id: str, building_id: str, start_ts, end_ts) -> MetricResult:
    """无效值率 = 负数或超 1e6 的 value_num / 总读数。"""
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE pr.value_num < 0 OR pr.value_num > %s) AS invalid
        FROM fact.point_reading pr
        JOIN core.point p ON p.id = pr.point_id
        WHERE p.tenant_id = %s AND p.building_id = %s
          AND pr.ts >= %s AND pr.ts < %s
    """, (INVALID_VALUE_THRESHOLD, tenant_id, building_id, start_ts, end_ts))
    total, invalid = cur.fetchone()
    rate = (invalid / total) if total > 0 else 0.0

    # 拉前 20 条 invalid 行做 details
    cur.execute("""
        SELECT pr.ts, p.point_code, pr.value_num
        FROM fact.point_reading pr
        JOIN core.point p ON p.id = pr.point_id
        WHERE p.tenant_id = %s AND p.building_id = %s
          AND pr.ts >= %s AND pr.ts < %s
          AND (pr.value_num < 0 OR pr.value_num > %s)
        ORDER BY pr.ts
        LIMIT 20
    """, (tenant_id, building_id, start_ts, end_ts, INVALID_VALUE_THRESHOLD))
    details = [{"ts": str(r[0]), "point_code": r[1], "value": r[2]} for r in cur.fetchall()]

    return MetricResult(
        metric="invalid_value_rate",
        value=round(rate, 6),
        threshold={"ok": 0.001, "warning": 0.01, "comparison": "le"},
        status=_judge_status("invalid_value_rate", rate),
        details=details,
    )


def _building_missing_gap_count(cur, tenant_id: str, building_id: str, start_ts, end_ts) -> MetricResult:
    """
    缺测时段数 = 相邻读数间隔 > 1.5 小时的次数。
    用 LAG 窗口函数算每个 point 的相邻间隔，超过 1.5h 的算一个 gap。
    """
    cur.execute("""
        WITH gaps AS (
            SELECT
                p.point_code,
                pr.ts AS cur_ts,
                LAG(pr.ts) OVER (PARTITION BY p.id ORDER BY pr.ts) AS prev_ts,
                EXTRACT(EPOCH FROM (pr.ts - LAG(pr.ts) OVER (PARTITION BY p.id ORDER BY pr.ts))) / 3600 AS gap_hours
            FROM fact.point_reading pr
            JOIN core.point p ON p.id = pr.point_id
            WHERE p.tenant_id = %s AND p.building_id = %s
              AND pr.ts >= %s AND pr.ts < %s
        )
        SELECT point_code, prev_ts, cur_ts, gap_hours
        FROM gaps
        WHERE gap_hours > 1.5
        ORDER BY cur_ts
        LIMIT 20
    """, (tenant_id, building_id, start_ts, end_ts))
    gap_rows = cur.fetchall()
    gap_count = len(gap_rows)

    # 查总数（不只是 LIMIT 20 的）
    cur.execute("""
        WITH gaps AS (
            SELECT EXTRACT(EPOCH FROM (pr.ts - LAG(pr.ts) OVER (PARTITION BY p.id ORDER BY pr.ts))) / 3600 AS gap_hours
            FROM fact.point_reading pr
            JOIN core.point p ON p.id = pr.point_id
            WHERE p.tenant_id = %s AND p.building_id = %s
              AND pr.ts >= %s AND pr.ts < %s
        )
        SELECT COUNT(*) FROM gaps WHERE gap_hours > 1.5
    """, (tenant_id, building_id, start_ts, end_ts))
    total_gaps = cur.fetchone()[0]

    details = [
        {
            "point_code": r[0],
            "from": str(r[1]) if r[1] else None,
            "to": str(r[2]),
            "gap_hours": round(float(r[3]), 2),
        }
        for r in gap_rows
    ]

    return MetricResult(
        metric="missing_gap_count",
        value=total_gaps,
        threshold={"ok": 0, "warning": 3, "comparison": "le"},
        status=_judge_status("missing_gap_count", total_gaps),
        details=details,
    )


def get_building_quality(
    building_id: str,
    tenant_id: str,
    start_ts: str,
    end_ts: str,
) -> dict:
    """按 building + time_range 算全部 6 个指标。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 先校验 building 存在 + 属于本租户
            cur.execute("""
                SELECT b.id, b.building_code, b.display_name
                FROM core.building b
                WHERE b.id = %s AND b.tenant_id = %s
            """, (building_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise ValueError("building 不存在或不属于当前租户")
            building_code, display_name = row[1], row[2]

            metrics = [
                _building_completeness(cur, tenant_id, building_id, start_ts, end_ts),
                _na_metric("duplicate_rate", "building 视角下 fact 表已去重，查 staging 不适用"),
                _building_invalid_value_rate(cur, tenant_id, building_id, start_ts, end_ts),
                _building_missing_gap_count(cur, tenant_id, building_id, start_ts, end_ts),
                _na_metric("timezone_parse_error_count", "building 视角下查 staging 不适用，看 batch 视角"),
                _na_metric("unit_normalization_count", "step 04 未记录单位转换，预留"),
            ]

    logger.info(
        "building quality: tenant={} building={} start={} end={} metrics={}",
        tenant_id, building_code, start_ts, end_ts,
        [(m.metric, m.status) for m in metrics],
    )
    return {
        "building_id": building_id,
        "building_code": building_code,
        "display_name": display_name,
        "time_range": {"start": start_ts, "end": end_ts},
        "metrics": [m.to_dict() for m in metrics],
    }


# ── batch 视角指标 ───────────────────────────────────────────

def _batch_duplicate_rate(cur, batch_id: str) -> MetricResult:
    """
    staging 里 (point_code, ts_text) 重复行数 / staging 总行数。
    重复 = 同一 (point_code, ts_text) 出现 >1 次，重复数 = count - 1。
    """
    cur.execute("""
        SELECT COUNT(*) FROM ingest.staging_reading WHERE batch_id = %s
    """, (batch_id,))
    total = cur.fetchone()[0]
    if total == 0:
        return _na_metric("duplicate_rate", "staging 为空")

    cur.execute("""
        WITH dups AS (
            SELECT point_code, ts_text, COUNT(*) AS cnt
            FROM ingest.staging_reading
            WHERE batch_id = %s
            GROUP BY point_code, ts_text
            HAVING COUNT(*) > 1
        )
        SELECT SUM(cnt - 1), COUNT(*) FROM dups
    """, (batch_id,))
    dup_rows, dup_groups = cur.fetchone()
    dup_rows = dup_rows or 0
    dup_groups = dup_groups or 0
    rate = dup_rows / total if total > 0 else 0.0

    # 前 20 个重复组
    cur.execute("""
        SELECT point_code, ts_text, COUNT(*) AS cnt
        FROM ingest.staging_reading
        WHERE batch_id = %s
        GROUP BY point_code, ts_text
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
        LIMIT 20
    """, (batch_id,))
    details = [
        {"point_code": r[0], "ts_text": r[1], "duplicate_count": r[2]}
        for r in cur.fetchall()
    ]

    return MetricResult(
        metric="duplicate_rate",
        value=round(rate, 6),
        threshold={"ok": 0.01, "warning": 0.05, "comparison": "le"},
        status=_judge_status("duplicate_rate", rate),
        details=details + [{"total_rows": total, "duplicate_rows": dup_rows, "duplicate_groups": dup_groups}],
    )


def _batch_timezone_parse_error(cur, batch_id: str) -> MetricResult:
    """staging 里 ts_text::timestamptz 失败的行数。"""
    # PG 里 ::timestamptz 转换失败的行不会自动 NULL，会直接报错中断查询。
    # 所以要先看哪些行能转、哪些不能。用 left(ts_text, 19)::timestamptz 看是否能 catch。
    # PG 没有内置 try_cast，但可以用正则匹配 ISO8601 格式粗筛。
    cur.execute("""
        SELECT COUNT(*) FROM ingest.staging_reading
        WHERE batch_id = %s
          AND ts_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T ][0-9]{2}:[0-9]{2}:[0-9]{2}'
    """, (batch_id,))
    bad_count = cur.fetchone()[0]

    cur.execute("""
        SELECT ts_text, point_code, row_no
        FROM ingest.staging_reading
        WHERE batch_id = %s
          AND ts_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T ][0-9]{2}:[0-9]{2}:[0-9]{2}'
        LIMIT 20
    """, (batch_id,))
    details = [
        {"ts_text": r[0], "point_code": r[1], "row_no": r[2]}
        for r in cur.fetchall()
    ]

    return MetricResult(
        metric="timezone_parse_error_count",
        value=bad_count,
        threshold={"ok": 0, "warning": 5, "comparison": "le"},
        status=_judge_status("timezone_parse_error_count", bad_count),
        details=details,
    )


def _batch_invalid_value_rate(cur, batch_id: str, tenant_id: str) -> MetricResult:
    """fact 表里 source_batch_id=batch_id 的负数或超限行 / 总数。"""
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE pr.value_num < 0 OR pr.value_num > %s) AS invalid
        FROM fact.point_reading pr
        WHERE pr.source_batch_id = %s AND pr.tenant_id = %s
    """, (INVALID_VALUE_THRESHOLD, batch_id, tenant_id))
    total, invalid = cur.fetchone()
    rate = (invalid / total) if total > 0 else 0.0

    cur.execute("""
        SELECT pr.ts, p.point_code, pr.value_num
        FROM fact.point_reading pr
        JOIN core.point p ON p.id = pr.point_id
        WHERE pr.source_batch_id = %s AND pr.tenant_id = %s
          AND (pr.value_num < 0 OR pr.value_num > %s)
        ORDER BY pr.ts
        LIMIT 20
    """, (batch_id, tenant_id, INVALID_VALUE_THRESHOLD))
    details = [{"ts": str(r[0]), "point_code": r[1], "value": r[2]} for r in cur.fetchall()]

    return MetricResult(
        metric="invalid_value_rate",
        value=round(rate, 6),
        threshold={"ok": 0.001, "warning": 0.01, "comparison": "le"},
        status=_judge_status("invalid_value_rate", rate),
        details=details,
    )


def get_batch_quality(batch_id: str, tenant_id: str) -> dict:
    """按 batch 算全部 6 个指标。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, dataset_source, target_type, status, row_count_total
                FROM ingest.import_batch
                WHERE id = %s AND tenant_id = %s
            """, (batch_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise ValueError("batch 不存在或不属于当前租户")

            metrics = [
                _na_metric("completeness", "batch 视角下时间跨度不适用，看 building 视角"),
                _batch_duplicate_rate(cur, batch_id),
                _batch_invalid_value_rate(cur, batch_id, tenant_id),
                _na_metric("missing_gap_count", "batch 视角下查 fact 时间序列不适用，看 building 视角"),
                _batch_timezone_parse_error(cur, batch_id),
                _na_metric("unit_normalization_count", "step 04 未记录单位转换，预留"),
            ]

    logger.info(
        "batch quality: tenant={} batch={} metrics={}",
        tenant_id, batch_id,
        [(m.metric, m.status) for m in metrics],
    )
    return {
        "batch_id": batch_id,
        "dataset_source": row[1],
        "target_type": row[2],
        "status": row[3],
        "row_count_total": row[4],
        "metrics": [m.to_dict() for m in metrics],
    }


# ── site overview ────────────────────────────────────────────

def get_site_overview(site_id: str, tenant_id: str, start_ts: str, end_ts: str) -> dict:
    """site 概览：列每个 building 的 completeness + missing_gap_count 摘要。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.id, s.site_code, s.site_name
                FROM core.site s
                WHERE s.id = %s AND s.tenant_id = %s
            """, (site_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise ValueError("site 不存在或不属于当前租户")
            site_code, site_name = row[1], row[2]

            cur.execute("""
                SELECT id, building_code, display_name
                FROM core.building
                WHERE site_id = %s AND tenant_id = %s
                ORDER BY building_code
            """, (site_id, tenant_id))
            buildings = cur.fetchall()

            # 对每个 building 算 completeness + missing_gap_count
            building_summaries = []
            for b in buildings:
                comp = _building_completeness(cur, tenant_id, str(b[0]), start_ts, end_ts)
                gap = _building_missing_gap_count(cur, tenant_id, str(b[0]), start_ts, end_ts)
                building_summaries.append({
                    "building_id": str(b[0]),
                    "building_code": b[1],
                    "display_name": b[2],
                    "completeness": comp.value,
                    "completeness_status": comp.status,
                    "missing_gap_count": gap.value,
                    "missing_gap_status": gap.status,
                })

    logger.info(
        "site overview: tenant={} site={} buildings={}",
        tenant_id, site_code, len(buildings),
    )
    return {
        "site_id": site_id,
        "site_code": site_code,
        "site_name": site_name,
        "time_range": {"start": start_ts, "end": end_ts},
        "buildings": building_summaries,
    }
