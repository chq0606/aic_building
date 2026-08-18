"""
上传服务：文件存储 + 校验 + staging 灌入 + building/point 自动创建。

核心流程：
1. 上传文件 → 存本地 + 写 upload_file 表 + 建 upload_session(UPLOADED)
2. mapping-suggest → 读 header 猜映射
3. 保存映射 → 更新 session.mapping_json(MAPPED)
4. validate → 按映射读文件转 long format，校验，记 row_count(VALIDATED/FAILED)
5. commit → 建 import_batch，写 staging_reading，session 置 COMMITTED

校验规则：timestamp 格式合法、value 数值合法、energy_type 在 core.energy_type 里。
building 不存在自动创建（building_code=building_id，site 用租户默认）。
任何校验错误不进 staging，停在 VALIDATED 状态等用户改。
"""
import hashlib
import json
import uuid
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd
from loguru import logger

from app.core.config import settings
from app.db.session import get_conn
from app.models.upload import MappingConfig
from app.services.wide_to_long import read_tabular, wide_to_long


# 文件存储根目录：backend/data/uploads/{tenant_id}/{upload_id}_{filename}
UPLOAD_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"


# ── 1. 文件存储 ─────────────────────────────────────────────

def save_upload_file(
    tenant_id: str,
    original_filename: str,
    file_content: BinaryIO,
    mime_type: str | None = None,
) -> tuple[str, Path]:
    """
    存文件到本地，写 upload_file 表，返回 (upload_file_id, storage_path)。

    文件名规则：{tenant_id}/{upload_id}_{original_filename}
    upload_id 先 INSERT 拿到，再决定存储路径。
    """
    # 先读全部内容算 sha256（顺便后面写文件用）
    content_bytes = file_content.read()
    sha256 = hashlib.sha256(content_bytes).hexdigest()

    # 幂等：同租户同 sha256 直接返回已有记录
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, storage_path FROM ingest.upload_file
                WHERE tenant_id = %s AND file_sha256 = %s
            """, (tenant_id, sha256))
            existing = cur.fetchone()
            if existing:
                logger.info("文件已存在（sha256 命中），复用 upload_file_id={}", existing[0])
                return str(existing[0]), Path(existing[1])

            # INSERT 拿 id（storage_path 先占位，拿到 id 后更新）
            cur.execute("""
                INSERT INTO ingest.upload_file
                    (tenant_id, original_filename, storage_path, mime_type,
                     size_bytes, file_sha256)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (tenant_id, original_filename, "", mime_type,
                  len(content_bytes), sha256))
            upload_id = str(cur.fetchone()[0])

            # 存储路径
            tenant_dir = UPLOAD_ROOT / tenant_id
            tenant_dir.mkdir(parents=True, exist_ok=True)
            # 文件名里 upload_id 前缀避免重名冲突
            safe_name = original_filename.replace("/", "_").replace("\\", "_")
            storage_path = tenant_dir / f"{upload_id}_{safe_name}"
            storage_path.write_bytes(content_bytes)

            cur.execute("""
                UPDATE ingest.upload_file SET storage_path = %s WHERE id = %s
            """, (str(storage_path), upload_id))
        conn.commit()

    logger.info("文件已保存: upload_file_id={} path={}", upload_id, storage_path)
    return upload_id, storage_path


def save_photo_file(
    tenant_id: str,
    original_filename: str,
    file_content: bytes,
    mime_type: str | None = None,
) -> tuple[str, Path]:
    """存照片到 storage/uploads/{tenant_id}/{photo_id}_{filename}。

    跟 save_upload_file 的差别:
      - 不算 sha256 (照片不需要去重, 同张照片传两次建两个 job 也合理)
      - 不写 ingest.upload_file 表 (那是 CSV/XLSX 专用, 表上没 image 类型字段)
      - photo_id 是 uuid4 字符串, reconstruction_job.input_upload_file_id
        字段直接存它 (字段是 uuid 不带 FK, 允许任意 uuid)
      - 文件名规则: {photo_id}_{original_filename}, 跟 CSV 一致方便反查

    照片生命周期跟随 reconstruction_job: job 删了照片也删 (delete_job
    调用方负责, 但 delete_job 只删 reconstruction 目录, 照片目录由后续
    清理任务兜底, 一期不实现)。
    """
    photo_id = str(uuid.uuid4())
    tenant_dir = settings.photo_upload_path / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)

    safe_name = original_filename.replace("/", "_").replace("\\", "_")
    storage_path = tenant_dir / f"{photo_id}_{safe_name}"
    storage_path.write_bytes(file_content)

    logger.info(
        "照片已保存: photo_id={} tenant={} path={} size={} mime={}",
        photo_id[:8], tenant_id[:8], storage_path, len(file_content), mime_type,
    )
    return photo_id, storage_path


def create_session(
    tenant_id: str,
    upload_file_id: str,
    target_type: str,
) -> str:
    """建 upload_session，返回 session_id。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ingest.upload_session
                    (tenant_id, upload_file_id, target_type, status)
                VALUES (%s, %s, %s, 'UPLOADED')
                RETURNING id
            """, (tenant_id, upload_file_id, target_type))
            session_id = str(cur.fetchone()[0])
        conn.commit()
    logger.info("upload_session 已创建: id={} target_type={}", session_id, target_type)
    return session_id


# ── 2. building / point 自动创建 ────────────────────────────

def _ensure_default_site(cur, tenant_id: str) -> str:
    """确保租户有默认 site（code='default'），返回 site_id。"""
    cur.execute("""
        SELECT id FROM core.site WHERE tenant_id = %s AND site_code = 'default'
    """, (tenant_id,))
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute("""
        INSERT INTO core.site (tenant_id, site_code, site_name, timezone, source_dataset)
        VALUES (%s, 'default', '默认园区', 'UTC', 'user_upload')
        RETURNING id
    """, (tenant_id,))
    return str(cur.fetchone()[0])


def _ensure_building(cur, tenant_id: str, site_id: str, building_code: str) -> str:
    """确保 building 存在，不存在就建。返回 building_id。"""
    cur.execute("""
        SELECT id FROM core.building
        WHERE tenant_id = %s AND building_code = %s
    """, (tenant_id, building_code))
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute("""
        INSERT INTO core.building
            (tenant_id, site_id, building_code, display_name, source_dataset)
        VALUES (%s, %s, %s, %s, 'user_upload')
        RETURNING id
    """, (tenant_id, site_id, building_code, building_code))
    logger.info("自动创建 building: code={} tenant={}", building_code, tenant_id)
    return str(cur.fetchone()[0])


def _ensure_point(
    cur, tenant_id: str, site_id: str, building_id: str,
    building_code: str, energy_type: str,
    floor_number: int | None = None,
) -> str:
    """
    确保 point 存在, 返回 point_id。

    floor_number=None:  楼栋级 METER, point_code = {building_code}__{energy_type}
    floor_number=int:   楼层级 SENSOR, point_code = {building_code}__F{n}__{energy_type}
                        floor_id 从 core.floor 查 (building_id + floor_number)

    楼层级 point 前置条件: core.floor 已有该楼层 (用户先传 floors.csv 或跑了
    auto-split)。查不到抛 ValueError, 提示先传 floors.csv。
    """
    if floor_number is None:
        point_code = f"{building_code}__{energy_type}"
        point_kind = "METER"
        floor_id = None
        point_name = f"{building_code} - {energy_type}"
    else:
        point_code = f"{building_code}__F{floor_number}__{energy_type}"
        point_kind = "SENSOR"
        point_name = f"{building_code} - {floor_number}F - {energy_type}"

        # 查 floor_id (building_id + floor_number)
        cur.execute("""
            SELECT id FROM core.floor
            WHERE building_id = %s AND floor_number = %s AND tenant_id = %s
        """, (building_id, floor_number, tenant_id))
        floor_row = cur.fetchone()
        if floor_row is None:
            raise ValueError(
                f"楼层 {building_code} F{floor_number} 不存在, "
                "请先上传 floors.csv 或在楼层分析页点'自动拆分'"
            )
        floor_id = str(floor_row[0])

    cur.execute("""
        SELECT id FROM core.point
        WHERE tenant_id = %s AND point_code = %s
    """, (tenant_id, point_code))
    row = cur.fetchone()
    if row:
        return str(row[0])

    # energy_type 必须在 core.energy_type 里（CHECK 约束）
    # measure_kind / unit_code 从 core.energy_type 查
    cur.execute("""
        SELECT measure_kind, unit_code FROM core.energy_type WHERE energy_type = %s
    """, (energy_type,))
    et_row = cur.fetchone()
    if et_row is None:
        raise ValueError(f"不支持的 energy_type: {energy_type}（不在 core.energy_type 字典里）")

    cur.execute("""
        INSERT INTO core.point
            (tenant_id, site_id, building_id, floor_id, point_code, point_name,
             point_kind, energy_type, measure_kind, unit_code,
             sample_interval_sec, source_dataset)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 3600, 'user_upload')
        RETURNING id
    """, (tenant_id, site_id, building_id, floor_id, point_code, point_name,
          point_kind, energy_type, et_row[0], et_row[1]))
    logger.info("自动创建 point: code={} kind={} floor={}",
                point_code, point_kind, floor_number)
    return str(cur.fetchone()[0])


# ── 3. 读文件 + 转长表 ──────────────────────────────────────

def read_as_long(file_path: Path, mapping: MappingConfig) -> pd.DataFrame:
    """
    按映射读文件，返回统一的长表 DataFrame。
    列固定：[timestamp, building_id, energy_type, value, unit（可选）]
    """
    df = read_tabular(file_path)

    if mapping.wide_melt:
        # 宽表 → 长表
        long_df = wide_to_long(df, mapping.wide_melt)
        # 宽表默认没有 unit 列，用 default_unit
        if mapping.default_unit:
            long_df["unit"] = mapping.default_unit
        return long_df

    # 长表：直接选列
    needed = [mapping.timestamp_col]
    if mapping.building_col:
        needed.append(mapping.building_col)
    if mapping.energy_col:
        needed.append(mapping.energy_col)
    if mapping.value_col:
        needed.append(mapping.value_col)
    if mapping.unit_col:
        needed.append(mapping.unit_col)
    # floor_col 可选: 不填 = 楼栋级 METER, 填了 = 楼层级 SENSOR (Step 11a-6)
    if mapping.floor_col:
        needed.append(mapping.floor_col)

    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"映射指定的列不在文件中: {missing}")

    long_df = df[needed].copy()
    long_df = long_df.rename(columns={
        mapping.timestamp_col: "timestamp",
        mapping.building_col or "": "building_id",
        mapping.energy_col or "": "energy_type",
        mapping.value_col or "": "value",
    })
    # floor_col 改名到 floor_number (跟 core.floor.floor_number 对齐)
    if mapping.floor_col:
        long_df = long_df.rename(columns={mapping.floor_col: "floor_number"})
    # 如果映射里没指定 building/energy/value，用 default
    if "building_id" not in long_df.columns:
        long_df["building_id"] = mapping.wide_melt.fixed_building if mapping.wide_melt else None
    if "energy_type" not in long_df.columns and mapping.default_energy_type:
        long_df["energy_type"] = mapping.default_energy_type
    if "value" not in long_df.columns:
        raise ValueError("长表映射必须指定 value_col")

    # unit 列
    if "unit" not in long_df.columns:
        long_df["unit"] = mapping.default_unit

    # value 转数值
    long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce")

    # floor_number 转整数 (允许 NaN = 楼栋级)
    if "floor_number" in long_df.columns:
        long_df["floor_number"] = pd.to_numeric(long_df["floor_number"], errors="coerce")

    # 去掉 value 为 NaN 的行
    long_df = long_df.dropna(subset=["value"])

    cols = ["timestamp", "building_id", "energy_type", "value", "unit"]
    if "floor_number" in long_df.columns:
        cols.append("floor_number")
    return long_df[cols]


# ── 4. 校验 ─────────────────────────────────────────────────

def validate_long_df(long_df: pd.DataFrame, mapping: MappingConfig) -> dict:
    """
    校验长表 DataFrame。

    返回 {row_count_total, row_count_valid, row_count_error, errors, can_commit}
    errors 最多 20 条，避免响应过大。
    """
    total = len(long_df)
    errors: list[dict] = []

    # timestamp 解析校验
    ts_series = pd.to_datetime(long_df["timestamp"], format=mapping.timestamp_format, errors="coerce", utc=False)
    # 时区转换：把 naive timestamp 当作 mapping.timezone，转 UTC
    if mapping.timezone and mapping.timezone != "UTC":
        try:
            ts_series = ts_series.dt.tz_localize(mapping.timezone).dt.tz_convert("UTC")
        except Exception:
            # 已经带时区了，直接转
            try:
                ts_series = ts_series.dt.tz_convert("UTC")
            except Exception:
                pass

    bad_ts = ts_series.isna()
    if bad_ts.any():
        for idx in long_df[bad_ts].head(10).index:
            errors.append({
                "row": int(idx),
                "field": "timestamp",
                "value": str(long_df.loc[idx, "timestamp"]),
                "error": "时间格式无法解析",
            })

    # value 数值校验（read_as_long 已经 to_numeric coerce，这里查 NaN）
    bad_value = long_df["value"].isna()
    if bad_value.any():
        for idx in long_df[bad_value].head(10).index:
            errors.append({
                "row": int(idx),
                "field": "value",
                "value": str(long_df.loc[idx, "value"]),
                "error": "数值无法解析",
            })

    # building_id 非空校验
    bad_building = long_df["building_id"].isna() | (long_df["building_id"] == "")
    if bad_building.any():
        for idx in long_df[bad_building].head(10).index:
            errors.append({
                "row": int(idx),
                "field": "building_id",
                "error": "building_id 为空",
            })

    # energy_type 非空校验
    bad_energy = long_df["energy_type"].isna() | (long_df["energy_type"] == "")
    if bad_energy.any():
        for idx in long_df[bad_energy].head(10).index:
            errors.append({
                "row": int(idx),
                "field": "energy_type",
                "error": "energy_type 为空",
            })

    valid_mask = ~bad_ts & ~bad_value & ~bad_building & ~bad_energy
    valid_count = int(valid_mask.sum())
    error_count = total - valid_count

    return {
        "row_count_total": total,
        "row_count_valid": valid_count,
        "row_count_error": error_count,
        "errors": errors[:20],
        "can_commit": error_count == 0 and total > 0,
    }


# ── 5. commit：写 staging ───────────────────────────────────

def commit_to_staging(
    session_id: str,
    tenant_id: str,
    file_path: Path,
    mapping: MappingConfig,
) -> tuple[str, int]:
    """
    commit：读文件 → 转长表 → 建 import_batch → 写 staging_reading。

    返回 (batch_id, row_count_inserted)。
    前置：session 必须 VALIDATED。
    """
    long_df = read_as_long(file_path, mapping)

    # 时区转换 + timestamp 字符串化（staging 表是 text）
    ts_series = pd.to_datetime(long_df["timestamp"], format=mapping.timestamp_format, errors="coerce")
    if mapping.timezone and mapping.timezone != "UTC":
        try:
            ts_series = ts_series.dt.tz_localize(mapping.timezone).dt.tz_convert("UTC")
        except Exception:
            try:
                ts_series = ts_series.dt.tz_convert("UTC")
            except Exception:
                pass
    # 转成 ISO 字符串存 staging，merge 时 ::timestamptz 解析
    long_df["ts_text"] = ts_series.dt.strftime("%Y-%m-%dT%H:%M:%S%z")

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. 自动创建 building / point（先收集所有 (building, energy) 组合）
            site_id = _ensure_default_site(cur, tenant_id)
            building_map: dict[str, str] = {}  # building_code → building_id
            point_map: dict[tuple[str, str, int | None], str] = {}  # (building_code, energy, floor|None) → point_id
            has_floor_col = "floor_number" in long_df.columns

            for _, row in long_df.iterrows():
                bcode = str(row["building_id"])
                etype = str(row["energy_type"])
                fnum = int(row["floor_number"]) if has_floor_col and pd.notna(row["floor_number"]) else None
                if bcode not in building_map:
                    building_map[bcode] = _ensure_building(cur, tenant_id, site_id, bcode)
                if (bcode, etype, fnum) not in point_map:
                    point_map[(bcode, etype, fnum)] = _ensure_point(
                        cur, tenant_id, site_id, building_map[bcode], bcode, etype, fnum,
                    )

            # 2. 建 import_batch（状态 LOADING，commit 完置 SUCCEEDED）
            cur.execute("""
                INSERT INTO ingest.import_batch
                    (tenant_id, dataset_source, target_type, status,
                     row_count_total, row_count_success, started_at)
                VALUES (%s, 'user_csv', 'POINT', 'LOADING', %s, 0, now())
                RETURNING id
            """, (tenant_id, len(long_df)))
            batch_id = str(cur.fetchone()[0])

            # 3. 写 staging_reading（COPY）
            # point_code 跟 _ensure_point 对齐: 楼栋级 {bcode}__{etype},
            # 楼层级 {bcode}__F{n}__{etype}
            buf = StringIO()
            for row_no, (_, row) in enumerate(long_df.iterrows(), start=1):
                bcode = str(row["building_id"])
                etype = str(row["energy_type"])
                fnum = int(row["floor_number"]) if has_floor_col and pd.notna(row["floor_number"]) else None
                if fnum is None:
                    point_code = f"{bcode}__{etype}"
                else:
                    point_code = f"{bcode}__F{fnum}__{etype}"
                value_text = str(row["value"])
                unit_text = str(row.get("unit", "") or "")
                buf.write(f"{batch_id}\t{row_no}\tdefault\t{bcode}\t{point_code}\t{row['ts_text']}\t{value_text}\t{unit_text}\t\n")
            buf.seek(0)
            cur.copy_from(buf, "staging_reading", sep="\t", null="",
                          columns=("batch_id", "row_no", "site_code", "building_code",
                                   "point_code", "ts_text", "value_text", "unit_text", "quality_text"))

            # 4. 更新 batch 状态
            # step 04 commit 只把 staging 灌完，状态置 LOADING（staging 已就绪，未 merge）
            # step 05 /imports/{id}/run 接口触发后才走 MERGING -> SUCCEEDED
            cur.execute("""
                UPDATE ingest.import_batch
                SET status = 'LOADING', row_count_success = %s, finished_at = now()
                WHERE id = %s
            """, (len(long_df), batch_id))

            # 5. 更新 session 状态
            cur.execute("""
                UPDATE ingest.upload_session
                SET status = 'COMMITTED',
                    row_count_total = %s,
                    row_count_valid = %s,
                    committed_batch_id = %s
                WHERE id = %s
            """, (len(long_df), len(long_df), batch_id, session_id))
        conn.commit()

    logger.info("commit 完成: session={} batch={} rows={}", session_id, batch_id, len(long_df))
    return batch_id, len(long_df)


# ── 6. FLOOR commit: 直接写 core.floor (不走 staging) ──────

# core.floor.floor_type CHECK 约束的合法值 (跟 step20_floor.sql 对齐)
VALID_FLOOR_TYPES = {
    'LOBBY', 'CLASSROOM', 'OFFICE', 'LAB', 'MECHANICAL',
    'LIBRARY', 'SPORTS', 'STUDENT_CENTER', 'OTHER',
}


def commit_floors(
    session_id: str,
    tenant_id: str,
    file_path: Path,
) -> tuple[str, int]:
    """
    FLOOR commit: 读 floors.csv -> 校验 -> 直接写 core.floor (source_dataset='user_uploaded')。

    跟 commit_to_staging 区别:
      - 不走 MappingConfig (FLOOR 字段固定, 不需要列映射)
      - 不写 staging_reading (FLOOR 不是时序数据, 没有 merge 步骤)
      - import_batch 直接置 SUCCEEDED (无 merge 步骤)

    CSV 列: building_id, floor_number, floor_name, floor_type, area_sqm, is_rooftop
    building_id 在 CSV 里是 building_code, commit 时按 code 查 id。
    """
    # 读 CSV, 跳过 # 注释行 (模板前几行是注释)
    df = pd.read_csv(file_path, comment='#', encoding='utf-8')

    required_cols = {'building_id', 'floor_number', 'floor_name',
                     'floor_type', 'area_sqm', 'is_rooftop'}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"楼层 CSV 缺少必填列: {missing}")

    # floor_type 合法性
    bad_types = set(df['floor_type'].dropna().unique()) - VALID_FLOOR_TYPES
    if bad_types:
        raise ValueError(
            f"不支持的 floor_type: {bad_types}, 可选: {sorted(VALID_FLOOR_TYPES)}"
        )

    # area_sqm > 0
    df['area_sqm'] = pd.to_numeric(df['area_sqm'], errors='coerce')
    if df['area_sqm'].isna().any() or (df['area_sqm'] <= 0).any():
        raise ValueError("area_sqm 必须是大于 0 的数值")

    # floor_number 整数
    df['floor_number'] = pd.to_numeric(df['floor_number'], errors='coerce')
    if df['floor_number'].isna().any():
        raise ValueError("floor_number 必须是整数")
    if not (df['floor_number'] == df['floor_number'].astype(int)).all():
        raise ValueError("floor_number 必须是整数 (不能是小数)")

    # is_rooftop -> bool
    df['is_rooftop'] = df['is_rooftop'].astype(str).str.lower().isin(['true', '1', 'yes'])

    # 楼栋内 floor_number 唯一
    dup_mask = df.duplicated(subset=['building_id', 'floor_number'], keep=False)
    if dup_mask.any():
        dups = df[dup_mask][['building_id', 'floor_number']].drop_duplicates().values.tolist()
        raise ValueError(f"楼栋内 floor_number 重复: {dups}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 查 building_code -> (building_id, site_id), 校验所有 building 都存在
            building_codes = df['building_id'].unique().tolist()
            cur.execute("""
                SELECT building_code, id, site_id FROM core.building
                WHERE tenant_id = %s AND building_code = ANY(%s)
            """, (tenant_id, building_codes))
            building_map = {row[0]: (str(row[1]), str(row[2])) for row in cur.fetchall()}

            missing_buildings = set(building_codes) - set(building_map.keys())
            if missing_buildings:
                raise ValueError(
                    f"楼层引用的 building_id 不存在: {sorted(missing_buildings)}, "
                    f"请先上传 buildings.csv 创建楼栋"
                )

            # 建 import_batch (直接 SUCCEEDED, 不走 merge)
            cur.execute("""
                INSERT INTO ingest.import_batch
                    (tenant_id, dataset_source, target_type, status,
                     row_count_total, row_count_success, started_at, finished_at)
                VALUES (%s, 'user_csv', 'FLOOR', 'SUCCEEDED', %s, %s, now(), now())
                RETURNING id
            """, (tenant_id, len(df), len(df)))
            batch_id = str(cur.fetchone()[0])

            # 写 core.floor (ON CONFLICT 楼层重复时更新)
            for _, row in df.iterrows():
                bid, site_id = building_map[row['building_id']]
                cur.execute("""
                    INSERT INTO core.floor
                        (tenant_id, site_id, building_id, floor_number, floor_name,
                         floor_type, area_sqm, is_rooftop, source_dataset, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'user_uploaded', '{}'::jsonb)
                    ON CONFLICT (building_id, floor_number) DO UPDATE SET
                        floor_name = EXCLUDED.floor_name,
                        floor_type = EXCLUDED.floor_type,
                        area_sqm = EXCLUDED.area_sqm,
                        is_rooftop = EXCLUDED.is_rooftop,
                        source_dataset = EXCLUDED.source_dataset
                """, (
                    tenant_id, site_id, bid,
                    int(row['floor_number']), str(row['floor_name']),
                    str(row['floor_type']), float(row['area_sqm']),
                    bool(row['is_rooftop']),
                ))

            # 更新 session 状态
            cur.execute("""
                UPDATE ingest.upload_session
                SET status = 'COMMITTED',
                    row_count_total = %s,
                    row_count_valid = %s,
                    committed_batch_id = %s
                WHERE id = %s
            """, (len(df), len(df), batch_id, session_id))
        conn.commit()

    logger.info("FLOOR commit 完成: session={} batch={} floors={}",
                session_id, batch_id, len(df))
    return batch_id, len(df)


def commit_buildings(
    session_id: str,
    tenant_id: str,
    file_path: Path,
) -> tuple[str, int]:
    """BUILDING commit: 读 buildings.csv -> 校验 -> 写 core.building (带 sqm/用途/层数)。

    跟 commit_floors 一样不走 staging/merge (建筑信息是元数据不是时序读数),
    import_batch 直接置 SUCCEEDED。

    CSV 列 (跟 template_service.BUILDINGS_CSV 对齐):
      building_id, building_name, site_code, primary_use, sqm, floors_count,
      year_built(可选), latitude(可选, core.building 无此列, 忽略), longitude(可选, 忽略)
    building_id 在 CSV 里是 building_code, commit 时按 (tenant_id, building_code) upsert。
    site_code 找已有 site, 找不到就自动建 (site_name = site_code)。
    """
    df = pd.read_csv(file_path, comment='#', encoding='utf-8')

    required_cols = {'building_id', 'building_name', 'site_code', 'primary_use', 'sqm', 'floors_count'}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"建筑 CSV 缺少必填列: {missing}")

    df['sqm'] = pd.to_numeric(df['sqm'], errors='coerce')
    if df['sqm'].isna().any() or (df['sqm'] <= 0).any():
        raise ValueError("sqm 必须是大于 0 的数值")

    df['floors_count'] = pd.to_numeric(df['floors_count'], errors='coerce')
    if df['floors_count'].isna().any() or (df['floors_count'] <= 0).any():
        raise ValueError("floors_count 必须是正整数")

    if 'year_built' in df.columns:
        df['year_built'] = pd.to_numeric(df['year_built'], errors='coerce')
    else:
        df['year_built'] = None

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 收集所有 site_code, 找已有 site; 没有的自动建 (site_name = site_code)
            site_codes = df['site_code'].dropna().astype(str).unique().tolist()
            if not site_codes:
                raise ValueError("site_code 列全为空, 至少指定一个园区")
            cur.execute("""
                SELECT site_code, id FROM core.site
                WHERE tenant_id = %s AND site_code = ANY(%s)
            """, (tenant_id, site_codes))
            site_map = {row[0]: str(row[1]) for row in cur.fetchall()}
            for code in site_codes:
                if code not in site_map:
                    cur.execute("""
                        INSERT INTO core.site (tenant_id, site_code, site_name, timezone, source_dataset)
                        VALUES (%s, %s, %s, 'UTC', 'user_upload')
                        RETURNING id
                    """, (tenant_id, code, code))
                    site_map[code] = str(cur.fetchone()[0])

            # 建 import_batch (直接 SUCCEEDED, 不走 merge)
            cur.execute("""
                INSERT INTO ingest.import_batch
                    (tenant_id, dataset_source, target_type, status,
                     row_count_total, row_count_success, started_at, finished_at)
                VALUES (%s, 'user_csv', 'BUILDING', 'SUCCEEDED', %s, %s, now(), now())
                RETURNING id
            """, (tenant_id, len(df), len(df)))
            batch_id = str(cur.fetchone()[0])

            # 写 core.building (ON CONFLICT 更新已有楼)
            for _, row in df.iterrows():
                site_id = site_map[str(row['site_code'])]
                yb = row['year_built']
                yb_val = int(yb) if pd.notna(yb) else None
                cur.execute("""
                    INSERT INTO core.building
                        (tenant_id, site_id, building_code, display_name,
                         primary_use, sqm, floors_count, year_built, source_dataset)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'user_upload')
                    ON CONFLICT (tenant_id, building_code) DO UPDATE SET
                        site_id = EXCLUDED.site_id,
                        display_name = EXCLUDED.display_name,
                        primary_use = EXCLUDED.primary_use,
                        sqm = EXCLUDED.sqm,
                        floors_count = EXCLUDED.floors_count,
                        year_built = EXCLUDED.year_built,
                        source_dataset = EXCLUDED.source_dataset
                """, (
                    tenant_id, site_id, str(row['building_id']), str(row['building_name']),
                    str(row['primary_use']), float(row['sqm']), int(row['floors_count']),
                    yb_val,
                ))

            # 更新 session 状态
            cur.execute("""
                UPDATE ingest.upload_session
                SET status = 'COMMITTED',
                    row_count_total = %s,
                    row_count_valid = %s,
                    committed_batch_id = %s
                WHERE id = %s
            """, (len(df), len(df), batch_id, session_id))
        conn.commit()

    logger.info("BUILDING commit 完成: session={} batch={} buildings={}",
                session_id, batch_id, len(df))
    return batch_id, len(df)
