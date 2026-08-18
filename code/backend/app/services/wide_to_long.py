"""
宽表转长表。

客户数据可能是宽表（每列一个测点），内部 canonical format 是长表
（一行一个读数）。这里用 pandas 的 melt 做。

输入：CSV/XLSX 文件路径 + WideMeltRule
输出：长表 DataFrame，列固定为 [timestamp, building_id, energy_type, value, unit]

拆解规则：
- underscore_2: 列名 "B1__electricity" → building=B1, energy=electricity
- underscore_3: 列名 "B1_floor2__electricity" → building=B1_floor2, energy=electricity
  （以最后一个 __ 分割，前面合并为 building）
- fixed_building: 所有 value 列都属同一个 building，building 从 fixed_building 字段取
  列名整体作为 energy_type
"""
import re
from pathlib import Path

import pandas as pd
from loguru import logger

from app.models.upload import WideMeltRule


def _split_column_name(col: str, rule: WideMeltRule) -> tuple[str, str]:
    """按 column_parse 规则把 value 列名拆成 (building, energy)。"""
    if rule.column_parse == "fixed_building":
        if not rule.fixed_building:
            raise ValueError("column_parse=fixed_building 时必须指定 fixed_building")
        return rule.fixed_building, col

    if rule.column_parse == "underscore_2":
        # B1__electricity → (B1, electricity)。用双下划线避免单下划线歧义
        if "__" not in col:
            raise ValueError(f"列名 '{col}' 不含双下划线分隔符，无法按 underscore_2 拆解")
        parts = col.rsplit("__", 1)
        return parts[0], parts[1]

    if rule.column_parse == "underscore_3":
        # 以最后一个 __ 分割，前面合并为 building
        if "__" not in col:
            raise ValueError(f"列名 '{col}' 不含双下划线分隔符，无法按 underscore_3 拆解")
        parts = col.rsplit("__", 1)
        return parts[0], parts[1]

    raise ValueError(f"不支持的 column_parse: {rule.column_parse}")


def wide_to_long(df: pd.DataFrame, rule: WideMeltRule) -> pd.DataFrame:
    """
    把宽表 DataFrame 转成长表。

    返回列固定：[timestamp, building_id, energy_type, value]
    timestamp 列名取自 rule.id_cols 里的第一个（约定 id_cols[0] 是 timestamp）。
    """
    if not rule.id_cols:
        raise ValueError("id_cols 不能为空")
    if not rule.value_cols:
        raise ValueError("value_cols 不能为空")

    # 校验 id_cols / value_cols 都在 df 里
    missing = [c for c in rule.id_cols + rule.value_cols if c not in df.columns]
    if missing:
        raise ValueError(f"映射指定的列不在文件中: {missing}")

    # pandas melt：id_vars 保留为标识列，value_vars 转成长表的 (variable, value) 两列
    long_df = df.melt(
        id_vars=rule.id_cols,
        value_vars=rule.value_cols,
        var_name="_col_name",
        value_name="value",
    )

    # 拆列名 → building + energy
    buildings = []
    energies = []
    for col in long_df["_col_name"]:
        b, e = _split_column_name(col, rule)
        buildings.append(b)
        energies.append(e)
    long_df["building_id"] = buildings
    long_df["energy_type"] = energies

    # 重命名 id_cols[0] → timestamp（约定）
    long_df = long_df.rename(columns={rule.id_cols[0]: "timestamp"})

    # 丢掉 NaN 值行（宽表里很多 cell 是空的，melt 后这些行 value=NaN）
    long_df = long_df.dropna(subset=["value"])

    # 只保留需要的列
    result = long_df[["timestamp", "building_id", "energy_type", "value"]].copy()
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result = result.dropna(subset=["value"])

    logger.info("宽表转长表：{} 行 → {} 行（去 NaN 后）", len(df), len(result))
    return result


def read_tabular(file_path: Path) -> pd.DataFrame:
    """
    读 CSV 或 XLSX，返回 DataFrame。
    XLSX 默认读第一个 sheet。
    """
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        # encoding 走 utf-8，separator 自动猜（pandas 默认用 csv sniffer）
        return pd.read_csv(file_path, encoding="utf-8")
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(file_path, sheet_name=0)
    raise ValueError(f"不支持的文件类型: {suffix}")
