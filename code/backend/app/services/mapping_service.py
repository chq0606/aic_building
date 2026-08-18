"""
列名猜测 + mapping_profile 管理。

suggested_mapping 是给前端"列名猜测"用的——读文件 header + 前 5 行，
用启发式规则猜哪列是 timestamp / building_id / energy_type / value。
猜不出来的字段留 None，让用户在向导里手动选。

启发式规则很保守，宁可留 None 也不乱猜。客户数据格式千差万别，
自动猜错比让用户手动选更糟。
"""
import json
from pathlib import Path

import pandas as pd
from loguru import logger

from app.models.upload import MappingConfig, WideMeltRule
from app.db.session import get_conn


# 列名猜常用的别名。key 是 canonical 名，value 是可能的客户列名（小写匹配）
COLUMN_ALIASES = {
    "timestamp": ["timestamp", "ts", "time", "datetime", "date", "时间", "时间戳", "读取时间"],
    "building_id": ["building_id", "building", "building_code", "bldg", "bldg_id", "楼", "楼号", "建筑id", "建筑编号"],
    "energy_type": ["energy_type", "energy", "type", "meter_type", "能源类型", "能源"],
    "value": ["value", "reading", "kwh", "consumption", "读数", "能耗", "用量", "数值"],
    "unit": ["unit", "units", "单位"],
    "quality": ["quality", "quality_code", "质量", "质量码"],
}


def _match_column(columns: list[str], canonical: str) -> str | None:
    """在 columns 里找匹配 canonical 的列。先精确匹配别名，再包含匹配。"""
    lower_cols = {c.lower(): c for c in columns}
    aliases = COLUMN_ALIASES.get(canonical, [])

    # 精确匹配
    for alias in aliases:
        if alias in lower_cols:
            return lower_cols[alias]

    # 包含匹配（列名包含别名）
    for alias in aliases:
        for lc, orig in lower_cols.items():
            if alias in lc:
                return orig

    return None


def suggest_mapping(file_path: Path) -> tuple[list[str], MappingConfig, list[dict]]:
    """
    读文件 header + 前 5 行，猜列映射。

    返回 (columns, suggested_mapping, sample_rows)。
    """
    from app.services.wide_to_long import read_tabular
    df = read_tabular(file_path)
    columns = list(df.columns)
    sample = df.head(5).fillna("").to_dict(orient="records")

    # 猜各列
    timestamp_col = _match_column(columns, "timestamp")
    building_col = _match_column(columns, "building_id")
    energy_col = _match_column(columns, "energy_type")
    value_col = _match_column(columns, "value")
    unit_col = _match_column(columns, "unit")
    quality_col = _match_column(columns, "quality")

    # 如果 value_col 没猜出来，但有多个数值列，可能是宽表
    # 宽表判断：timestamp 列找到了，且除了 timestamp 之外有多个数值列
    is_wide = False
    if timestamp_col and not value_col:
        numeric_cols = [c for c in columns if c != timestamp_col and pd.api.types.is_numeric_dtype(df[c])]
        if len(numeric_cols) > 1:
            is_wide = True
            logger.info("检测到宽表格式，value 列 = {} 个数值列", len(numeric_cols))
            # 宽表的 mapping：timestamp + wide_melt
            suggested = MappingConfig(
                timestamp_col=timestamp_col,
                building_col=None,
                energy_col=None,
                value_col=None,
                unit_col=unit_col,
                quality_col=quality_col,
                wide_melt=WideMeltRule(
                    id_cols=[timestamp_col],
                    value_cols=numeric_cols[:20],  # 最多取前 20 列做样例，避免响应过大
                    column_parse="underscore_2",
                ),
            )
            return columns, suggested, sample

    # 长表
    suggested = MappingConfig(
        timestamp_col=timestamp_col or "",
        building_col=building_col,
        energy_col=energy_col,
        value_col=value_col,
        unit_col=unit_col,
        quality_col=quality_col,
        wide_melt=None,
    )
    logger.info("列名猜测完成: timestamp={}, building={}, energy={}, value={}",
                timestamp_col, building_col, energy_col, value_col)
    return columns, suggested, sample


# ── mapping_profile 管理 ────────────────────────────────────

def save_profile(
    tenant_id: str,
    profile_name: str,
    template_type: str,
    mapping: MappingConfig,
    unit_rules: dict,
) -> str:
    """保存为 mapping_profile，返回 profile_id。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ingest.mapping_profile
                    (tenant_id, profile_name, template_type, timezone,
                     timestamp_format, mapping_json, unit_rules)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                tenant_id, profile_name, template_type,
                mapping.timezone, mapping.timestamp_format,
                json.dumps(mapping.model_dump(), ensure_ascii=False),
                json.dumps(unit_rules, ensure_ascii=False),
            ))
            profile_id = str(cur.fetchone()[0])
        conn.commit()
    logger.info("mapping_profile 已保存: id={} name={} tenant={}", profile_id, profile_name, tenant_id)
    return profile_id
