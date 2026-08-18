"""
BDG2 demo 数据导入。

从 code/seed_bdg2.py 迁移过来,改造点:
- 连接配置走 settings.database_url,不再用 PGPASSWORD 散装五件套
- 日志走 loguru,不再 print
- 加 reset_demo_tenant():只清 demo 租户的数据,其他注册用户的数据保留
- 加 run_bdg2_seed(reset):主入口,串起清空 → 灌 staging → merge → 聚合

数据文件位置:backend 上溯两级到 aic_building/,再进 extracted_data/。
clone 项目后只要 extracted_data/ 在,这个模块就能跑。
"""
import csv
from io import StringIO
from pathlib import Path

from loguru import logger

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import get_conn


# 数据文件:backend/ 上溯两级到 aic_building/
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BACKEND_ROOT.parent.parent / "extracted_data"
METADATA_CSV = DATA_DIR / "metadata.csv"
READINGS_CSV = DATA_DIR / "readings_2017.csv"
WEATHER_CSV = DATA_DIR / "weather_2017.csv"

# demo 租户/site 常量,和原 seed_bdg2.py 保持一致
TENANT_CODE = "demo"
TENANT_NAME = "BDG2 演示数据"
SITE_CODE = "Bobcat"
SITE_NAME = "Bobcat Site (BDG2)"
SITE_TZ = "US/Mountain"

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo123"

# BDG2 的 energy_type → (measure_kind, unit_code) 映射
# 和原 seed_bdg2.py 完全一致,不要乱改
ENERGY_TYPE_TO_MEASURE = {
    "electricity":   ("energy_kwh",         "kWh"),
    "hotwater":      ("energy_kwh_thermal", "kWh_thermal"),
    "chilledwater":  ("energy_kwh_thermal", "kWh_thermal"),
    "steam":         ("energy_kwh_thermal", "kWh_thermal"),
    "gas":           ("energy_kwh",         "kWh"),
    "water":         ("volume_liter",       "L"),
    "irrigation":    ("volume_liter",       "L"),
    "solar":         ("energy_kwh",         "kWh"),
}


def reset_demo_tenant() -> None:
    """
    清空 demo 租户的所有业务数据,保留 demo 租户行和 demo 用户行。

    清理范围:
    - mart.building_daily_energy (按 tenant_id)
    - fact.point_reading (按 tenant_id)
    - fact.weather_reading (按 tenant_id)
    - core.point / core.building (CASCADE 会带走,显式删更可控)
    - core.site (demo 租户名下的)

    不清:
    - core.tenant(demo 行保留,避免重新建租户时 ID 变化)
    - core."user"(demo 用户保留,避免每次 reset 都要重新建用户)
    - knowledge.*(知识库和 demo 数据无关)
    """
    logger.info("开始清空 demo 租户的业务数据...")
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 拿 demo tenant_id,顺带判断租户是否存在
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code = %s", (TENANT_CODE,))
            row = cur.fetchone()
            if row is None:
                logger.warning("demo 租户不存在,跳过清空(首次 seed 时会自动创建)")
                conn.rollback()
                return
            tenant_id = row[0]

            # 按依赖顺序删:fact → mart → core.point → core.building → core.site
            # core.building/point 有 ON DELETE CASCADE 到 tenant,但显式删更清晰
            cur.execute("DELETE FROM mart.building_daily_energy WHERE tenant_id = %s", (tenant_id,))
            logger.info("  mart.building_daily_energy 已清")
            cur.execute("DELETE FROM fact.point_reading WHERE tenant_id = %s", (tenant_id,))
            logger.info("  fact.point_reading 已清")
            cur.execute("DELETE FROM fact.weather_reading WHERE tenant_id = %s", (tenant_id,))
            logger.info("  fact.weather_reading 已清")
            cur.execute("DELETE FROM core.point WHERE tenant_id = %s", (tenant_id,))
            logger.info("  core.point 已清")
            cur.execute("DELETE FROM core.building WHERE tenant_id = %s", (tenant_id,))
            logger.info("  core.building 已清")
            cur.execute("DELETE FROM core.site WHERE tenant_id = %s", (tenant_id,))
            logger.info("  core.site 已清")
        conn.commit()
    logger.info("demo 租户业务数据清空完成")


def _load_metadata():
    """读 metadata.csv,返回 building 列表。和原 seed_bdg2.py 一致。"""
    buildings = []
    with open(METADATA_CSV, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            def _int(v):
                try:
                    return int(float(v)) if v else None
                except (ValueError, TypeError):
                    return None
            def _float(v):
                try:
                    return float(v) if v else None
                except (ValueError, TypeError):
                    return None
            buildings.append({
                "building_code": row["building_id"],
                "display_name": row["building_id"],
                "primary_use":  row.get("primaryspaceusage") or None,
                "sub_use":       row.get("sub_primaryspaceusage") or None,
                "sqm":           _float(row.get("sqm")),
                "sqft":          _float(row.get("sqft")),
                "floors_count":  _int(row.get("numberoffloors")),
                "year_built":    _int(row.get("yearbuilt")),
                "has_electricity": row.get("electricity") == "Yes",
                "has_hotwater":     row.get("hotwater") == "Yes",
                "has_chilledwater": row.get("chilledwater") == "Yes",
                "has_steam":        row.get("steam") == "Yes",
                "has_water":        row.get("water") == "Yes",
                "has_irrigation":   row.get("irrigation") == "Yes",
                "has_solar":        row.get("solar") == "Yes",
                "has_gas":          row.get("gas") == "Yes",
            })
    return buildings


def _load_energy_types_present():
    """扫 readings_2017.csv,看实际有哪些 energy_type。"""
    present = set()
    with open(READINGS_CSV, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            present.add(row["energy_type"])
    return present


def run_bdg2_seed(reset: bool = False) -> dict:
    """
    跑完整 BDG2 seed 流程。

    流程:reset(可选) → 建 tenant/site → 建 building/point →
         灌 staging_reading → 灌 staging_weather → merge 到 fact →
         生成日聚合 → ANALYZE → 重建 IVFFlat 索引(知识库非空时) →
         确保 demo 用户存在

    返回统计 dict,CLI 和 API 都用这个返回值。
    """
    # 先验证数据文件在
    for p in [METADATA_CSV, READINGS_CSV, WEATHER_CSV]:
        if not p.exists():
            raise FileNotFoundError(f"BDG2 数据文件不存在: {p}")

    stats = {
        "buildings": 0, "points": 0,
        "readings": 0, "weather": 0,
        "daily_energy": 0,
        "ivfflat_rebuilt": False,
        "demo_user_ensured": False,
    }

    if reset:
        reset_demo_tenant()

    logger.info("开始 BDG2 seed 流程")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SET search_path TO core, ingest, fact, mart, knowledge, public")

        # 1. 读 metadata + 扫 readings 拿 energy_type
        logger.info("读 metadata.csv + 扫描 readings...")
        buildings = _load_metadata()
        energy_types_present = _load_energy_types_present()
        logger.info("  楼栋数: {}, 实际能源类型: {}", len(buildings), sorted(energy_types_present))

        # 2. 写 tenant / site(幂等)
        logger.info("写 core.tenant / core.site ...")
        cur.execute("""
            INSERT INTO core.tenant (tenant_code, tenant_name)
            VALUES (%s, %s)
            ON CONFLICT (tenant_code) DO UPDATE SET tenant_name = EXCLUDED.tenant_name
            RETURNING id
        """, (TENANT_CODE, TENANT_NAME))
        tenant_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO core.site (tenant_id, site_code, site_name, timezone, source_dataset)
            VALUES (%s, %s, %s, %s, 'bdg2')
            ON CONFLICT (tenant_id, site_code) DO UPDATE SET site_name = EXCLUDED.site_name
            RETURNING id
        """, (tenant_id, SITE_CODE, SITE_NAME, SITE_TZ))
        site_id = cur.fetchone()[0]

        # 3. 写 building / point
        logger.info("写 core.building / core.point ...")
        building_id_map = {}
        point_id_map = {}
        for b in buildings:
            cur.execute("""
                INSERT INTO core.building
                    (tenant_id, site_id, building_code, display_name, primary_use, sub_use,
                     sqm, sqft, floors_count, year_built, source_dataset)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'bdg2')
                ON CONFLICT (tenant_id, building_code) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    primary_use  = EXCLUDED.primary_use,
                    sub_use      = EXCLUDED.sub_use,
                    sqm          = EXCLUDED.sqm,
                    sqft         = EXCLUDED.sqft,
                    floors_count = EXCLUDED.floors_count,
                    year_built   = EXCLUDED.year_built
                RETURNING id
            """, (tenant_id, site_id, b["building_code"], b["display_name"],
                  b["primary_use"], b["sub_use"], b["sqm"], b["sqft"],
                  b["floors_count"], b["year_built"]))
            bid = cur.fetchone()[0]
            building_id_map[b["building_code"]] = bid

            for et in energy_types_present:
                if b.get(f"has_{et}"):
                    measure_kind, unit_code = ENERGY_TYPE_TO_MEASURE[et]
                    point_code = f"{b['building_code']}__{et}"
                    point_name = f"{b['building_code']} - {et}"
                    cur.execute("""
                        INSERT INTO core.point
                            (tenant_id, site_id, building_id, point_code, point_name,
                             point_kind, energy_type, measure_kind, unit_code,
                             sample_interval_sec, source_dataset)
                        VALUES (%s, %s, %s, %s, %s, 'METER', %s, %s, %s, 3600, 'bdg2')
                        ON CONFLICT (tenant_id, point_code) DO UPDATE SET
                            point_name   = EXCLUDED.point_name,
                            energy_type  = EXCLUDED.energy_type,
                            measure_kind = EXCLUDED.measure_kind,
                            unit_code    = EXCLUDED.unit_code
                        RETURNING id
                    """, (tenant_id, site_id, bid, point_code, point_name,
                          et, measure_kind, unit_code))
                    pid = cur.fetchone()[0]
                    point_id_map[(b["building_code"], et)] = pid

        stats["buildings"] = len(building_id_map)
        stats["points"] = len(point_id_map)
        logger.info("  building 写入: {}, point 写入: {}", len(building_id_map), len(point_id_map))

        # 4. 灌 staging_reading
        logger.info("灌 ingest.staging_reading ...")
        cur.execute("""
            INSERT INTO ingest.import_batch
                (tenant_id, dataset_source, target_type, status,
                 row_count_total, row_count_success, started_at, finished_at)
            VALUES (%s, 'bdg2', 'POINT', 'LOADING', 0, 0, now(), now())
            RETURNING id
        """, (tenant_id,))
        reading_batch_id = cur.fetchone()[0]

        buf = StringIO()
        writer = csv.writer(buf, delimiter="\t")
        row_no = 0
        inserted = 0
        skipped = 0
        with open(READINGS_CSV, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                bcode = row["building_id"]
                et = row["energy_type"]
                if (bcode, et) not in point_id_map:
                    skipped += 1
                    continue
                point_code = f"{bcode}__{et}"
                row_no += 1
                writer.writerow([
                    reading_batch_id, row_no, SITE_CODE, bcode, point_code,
                    row["timestamp"], row["value"], row["unit"], ""
                ])
                inserted += 1
        buf.seek(0)
        # copy_from 不认 schema 前缀,前面已经 SET search_path 了
        cur.copy_from(buf, "staging_reading", sep="\t", null="",
                      columns=("batch_id", "row_no", "site_code", "building_code",
                               "point_code", "ts_text", "value_text", "unit_text", "quality_text"))

        cur.execute("""
            UPDATE ingest.import_batch
            SET row_count_total = %s, row_count_success = %s, status = 'SUCCEEDED', finished_at = now()
            WHERE id = %s
        """, (inserted, inserted, reading_batch_id))
        logger.info("  staging_reading 灌入: {} 行, 跳过: {}", inserted, skipped)

        # 5. 灌 staging_weather
        logger.info("灌 ingest.staging_weather ...")
        cur.execute("""
            INSERT INTO ingest.import_batch
                (tenant_id, dataset_source, target_type, status,
                 row_count_total, row_count_success, started_at, finished_at)
            VALUES (%s, 'bdg2', 'WEATHER', 'LOADING', 0, 0, now(), now())
            RETURNING id
        """, (tenant_id,))
        weather_batch_id = cur.fetchone()[0]

        buf2 = StringIO()
        writer2 = csv.writer(buf2, delimiter="\t")
        w_row_no = 0
        with open(WEATHER_CSV, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                w_row_no += 1
                writer2.writerow([
                    weather_batch_id, w_row_no, tenant_id, row["site_id"],
                    row["timestamp"],
                    row.get("airTemperature", ""),
                    row.get("cloudCoverage", ""),
                    row.get("dewTemperature", ""),
                    row.get("precipDepth1HR", ""),
                    row.get("precipDepth6HR", ""),
                    row.get("seaLvlPressure", ""),
                    row.get("windDirection", ""),
                    row.get("windSpeed", ""),
                ])
        buf2.seek(0)
        cur.copy_from(buf2, "staging_weather", sep="\t", null="",
                      columns=("batch_id", "row_no", "tenant_id", "site_code", "ts_text",
                               "air_temp_text", "cloud_text", "dew_temp_text",
                               "precip_1hr_text", "precip_6hr_text", "pressure_text",
                               "wind_dir_text", "wind_speed_text"))

        cur.execute("""
            UPDATE ingest.import_batch
            SET row_count_total = %s, row_count_success = %s, status = 'SUCCEEDED', finished_at = now()
            WHERE id = %s
        """, (w_row_no, w_row_no, weather_batch_id))
        logger.info("  staging_weather 灌入: {} 行", w_row_no)

        # 6. merge staging_reading → fact.point_reading
        logger.info("merge staging_reading → fact.point_reading ...")
        cur.execute("""
            INSERT INTO fact.point_reading (
                tenant_id, site_id, building_id, point_id, ts, value_num, quality_code, source_batch_id
            )
            SELECT
                ib.tenant_id, p.site_id, p.building_id, p.id,
                s.ts_text::timestamptz, s.value_text::double precision,
                COALESCE(NULLIF(s.quality_text, ''), 'GOOD'), ib.id
            FROM ingest.staging_reading s
            JOIN ingest.import_batch ib ON ib.id = s.batch_id
            JOIN core.point p
              ON p.tenant_id = ib.tenant_id
             AND p.point_code = s.point_code
            JOIN core.building b
              ON b.id = p.building_id
             AND b.building_code = s.building_code
            WHERE s.batch_id = %s
            ON CONFLICT (point_id, ts) DO UPDATE
            SET
                value_num       = EXCLUDED.value_num,
                quality_code    = EXCLUDED.quality_code,
                source_batch_id = EXCLUDED.source_batch_id,
                updated_at      = now()
        """, (reading_batch_id,))
        reading_merged = cur.rowcount
        stats["readings"] = reading_merged
        logger.info("  point_reading 写入/更新: {} 行", reading_merged)

        # 7. merge staging_weather → fact.weather_reading
        logger.info("merge staging_weather → fact.weather_reading ...")
        cur.execute("""
            INSERT INTO fact.weather_reading (
                tenant_id, site_id, ts,
                air_temp_c, cloud_cover_pct, dew_temp_c,
                precip_mm, precip_6hr_mm, sea_level_pressure_hpa,
                wind_direction_deg, wind_speed_mps, source_batch_id
            )
            SELECT
                sw.tenant_id, st.id AS site_id, sw.ts_text::timestamptz,
                sw.air_temp_text::double precision, sw.cloud_text::double precision,
                sw.dew_temp_text::double precision, sw.precip_1hr_text::double precision,
                sw.precip_6hr_text::double precision, sw.pressure_text::double precision,
                sw.wind_dir_text::double precision, sw.wind_speed_text::double precision, ib.id
            FROM ingest.staging_weather sw
            JOIN ingest.import_batch ib ON ib.id = sw.batch_id
            JOIN core.site st
              ON st.tenant_id = sw.tenant_id
             AND st.site_code = sw.site_code
            WHERE sw.batch_id = %s
            ON CONFLICT (site_id, ts) DO UPDATE
            SET
                air_temp_c            = EXCLUDED.air_temp_c,
                cloud_cover_pct       = EXCLUDED.cloud_cover_pct,
                dew_temp_c            = EXCLUDED.dew_temp_c,
                precip_mm             = EXCLUDED.precip_mm,
                precip_6hr_mm         = EXCLUDED.precip_6hr_mm,
                sea_level_pressure_hpa = EXCLUDED.sea_level_pressure_hpa,
                wind_direction_deg    = EXCLUDED.wind_direction_deg,
                wind_speed_mps        = EXCLUDED.wind_speed_mps,
                source_batch_id       = EXCLUDED.source_batch_id
        """, (weather_batch_id,))
        weather_merged = cur.rowcount
        stats["weather"] = weather_merged
        logger.info("  weather_reading 写入/更新: {} 行", weather_merged)

        # 8. 生成日聚合 mart.building_daily_energy
        logger.info("生成 mart.building_daily_energy ...")
        cur.execute("""
            INSERT INTO mart.building_daily_energy
                (tenant_id, building_id, energy_type, date,
                 total_kwh, max_kwh, min_kwh, avg_kwh, hour_count, eui_kwh_per_m2,
                 source_batch_id)
            SELECT
                p.tenant_id, p.building_id, p.energy_type, pr.ts::date AS date,
                SUM(pr.value_num) AS total_kwh, MAX(pr.value_num) AS max_kwh,
                MIN(pr.value_num) AS min_kwh, AVG(pr.value_num) AS avg_kwh,
                COUNT(*) AS hour_count,
                ROUND(CAST(SUM(pr.value_num) / NULLIF(b.sqm, 0) AS numeric), 3) AS eui_kwh_per_m2,
                %s
            FROM fact.point_reading pr
            JOIN core.point p ON p.id = pr.point_id
            JOIN core.building b ON b.id = p.building_id
            WHERE pr.source_batch_id = %s
              AND p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal')
            GROUP BY p.tenant_id, p.building_id, p.energy_type, pr.ts::date, b.sqm
            ON CONFLICT (building_id, energy_type, date) DO UPDATE
            SET
                total_kwh      = EXCLUDED.total_kwh,
                max_kwh        = EXCLUDED.max_kwh,
                min_kwh        = EXCLUDED.min_kwh,
                avg_kwh        = EXCLUDED.avg_kwh,
                hour_count     = EXCLUDED.hour_count,
                eui_kwh_per_m2 = EXCLUDED.eui_kwh_per_m2,
                source_batch_id = EXCLUDED.source_batch_id
        """, (reading_batch_id, reading_batch_id))
        daily_rows = cur.rowcount
        stats["daily_energy"] = daily_rows
        logger.info("  building_daily_energy 写入: {} 行", daily_rows)

        # 9. ANALYZE,让查询计划器拿到最新统计
        logger.info("ANALYZE 关键表 ...")
        cur.execute("ANALYZE fact.point_reading")
        cur.execute("ANALYZE fact.weather_reading")
        cur.execute("ANALYZE mart.building_daily_energy")
        cur.execute("ANALYZE core.building")
        cur.execute("ANALYZE core.point")

        # 10. 清 staging(下次 seed 不会污染)
        cur.execute("DELETE FROM ingest.staging_reading WHERE batch_id = %s", (reading_batch_id,))
        cur.execute("DELETE FROM ingest.staging_weather WHERE batch_id = %s", (weather_batch_id,))

        conn.commit()

    # 11. 重建 IVFFlat 索引(知识库非空时)
    from app.seed.ivfflat_rebuild import rebuild_ivfflat_if_needed
    stats["ivfflat_rebuilt"] = rebuild_ivfflat_if_needed()

    # 12. 确保 demo 用户存在
    from app.seed.demo_user import ensure_demo_user
    try:
        ensure_demo_user()
        stats["demo_user_ensured"] = True
    except Exception as e:
        logger.warning("demo 用户初始化失败(不阻断 seed): {}", e)

    # 13. 自动接调楼层虚拟数据生成 (Step 20)
    # 依赖: 楼栋级 METER point + fact.point_reading 已灌入 (上面步骤 3-8 完成)
    # reset 跟 bdg2 一致: bdg2 reset 时 floor 也 reset (清旧楼层数据再重建)
    from app.seed.floor_synthetic import run_floor_synthetic_seed
    try:
        floor_stats = run_floor_synthetic_seed(reset=reset)
        stats["floor_synthetic"] = floor_stats
    except Exception as e:
        logger.exception("楼层虚拟数据生成失败(不阻断 bdg2 seed): {}", e)

    logger.info("BDG2 seed 完成: {}", stats)
    return stats
