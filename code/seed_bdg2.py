"""
BDG2 demo 数据导入脚本
输入: extracted_data/metadata.csv, readings_2017.csv, weather_2017.csv
输出: 写入 core.tenant/site/building/point + ingest.staging_reading/staging_weather
依赖: psycopg2

SQL 文件本身干不了的几件事:
1. metadata.csv 转 core.tenant/site/building/point 四张拓扑表
   (CSV 字段名和表字段对不上: building_id->building_code, primaryspaceusage->primary_use 等)
2. readings_2017.csv 灌进 ingest.staging_reading
   (CSV 没 batch_id/row_no/site_code/point_code, 脚本要补; 字段名也要改)
3. weather_2017.csv 灌进 ingest.staging_weather
   (字段名映射: airTemperature->air_temp_text 等)

用法:
    # 本机直连 (legacy 兼容)
    set PGPASSWORD=你的密码
    python code/seed_bdg2.py

    # docker compose / 容器内 (推荐, 复用 backend 的 DATABASE_URL)
    docker compose exec backend python code/seed_bdg2.py

DATABASE_URL 优先于 PGPASSWORD, 这样 backend 容器直接用同一个环境变量,
不必再单独维护 PGPASSWORD。
"""
import csv
import os
import sys
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

import psycopg2

# ── 连接配置 ────────────────────────────────────────────────
# 优先读 DATABASE_URL (跟 backend 共用), 兼容老的 PGPASSWORD 直连模式
_db_url = os.environ.get("DATABASE_URL", "")

if _db_url:
    # 容器 / docker-compose 模式: postgresql://user:pwd@host:port/dbname
    _parsed = urlparse(_db_url)
    DB_CONFIG = {
        "host": _parsed.hostname or "localhost",
        "port": _parsed.port or 5432,
        "dbname": (_parsed.path or "/aic_building")[1:],
        "user": _parsed.username or "postgres",
        "password": _parsed.password or "",
    }
else:
    # legacy 直连模式: 用 PGPASSWORD, host=db 默认 localhost
    DB_CONFIG = {
        "host": "localhost",
        "port": 5432,
        "dbname": "aic_building",
        "user": "postgres",
        "password": os.environ.get("PGPASSWORD", ""),
    }
    if not DB_CONFIG["password"]:
        print("ERROR: 请先设置 DATABASE_URL 或 PGPASSWORD 环境变量")
        print("  docker:       DATABASE_URL 已由 compose 注入, 直接 docker compose exec backend python code/seed_bdg2.py")
        print("  Windows CMD:  set PGPASSWORD=你的密码")
        print("  Windows PS:   $env:PGPASSWORD='你的密码'")
        print("  Linux/Mac:    export PGPASSWORD=你的密码")
        sys.exit(1)

# ── 数据文件路径 ────────────────────────────────────────────
# 容器内 /app/extracted_data (docker-compose volume), 本机走项目根 extracted_data/
_env_data_dir = os.environ.get("EXTRACTED_DATA_DIR", "")
if _env_data_dir:
    DATA_DIR = Path(_env_data_dir)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = PROJECT_ROOT / "extracted_data"

METADATA_CSV = DATA_DIR / "metadata.csv"
READINGS_CSV = DATA_DIR / "readings_2017.csv"
WEATHER_CSV  = DATA_DIR / "weather_2017.csv"

for p in [METADATA_CSV, READINGS_CSV, WEATHER_CSV]:
    if not p.exists():
        print(f"ERROR: 数据文件不存在: {p}")
        sys.exit(1)

# ── demo tenant / site ─────────────────────────────────────
# demo 数据用专属 tenant, 和后续真实用户数据隔离
TENANT_CODE = "demo"
TENANT_NAME = "BDG2 演示数据"
SITE_CODE   = "Bobcat"
SITE_NAME   = "Bobcat Site (BDG2)"
SITE_TZ     = "US/Mountain"


def load_metadata():
    """读 metadata.csv, 返回 building 列表"""
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


def load_energy_types_present():
    """扫 readings_2017.csv, 看实际有哪些 energy_type"""
    present = set()
    with open(READINGS_CSV, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            present.add(row["energy_type"])
    return present


def main():
    print(f"[1/6] 连接 PostgreSQL ({DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}) ...")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET search_path TO core, ingest, fact, mart, knowledge, public;")

    print("[2/6] 读 metadata.csv + 扫描 readings ...")
    buildings = load_metadata()
    print(f"      楼栋数: {len(buildings)}")
    energy_types_present = load_energy_types_present()
    print(f"      实际能源类型: {sorted(energy_types_present)}")

    # ── 3. 写 tenant / site ───────────────────────────────
    print("[3/6] 写 core.tenant / core.site ...")
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

    # ── 4. 写 building / point ────────────────────────────
    print("[4/6] 写 core.building / core.point ...")
    energy_type_to_measure = {
        "electricity":   ("energy_kwh",         "kWh"),
        "hotwater":      ("energy_kwh_thermal", "kWh_thermal"),
        "chilledwater":  ("energy_kwh_thermal", "kWh_thermal"),
        "steam":         ("energy_kwh_thermal", "kWh_thermal"),
        "gas":           ("energy_kwh",         "kWh"),
        "water":         ("volume_liter",       "L"),
        "irrigation":    ("volume_liter",       "L"),
        "solar":         ("energy_kwh",         "kWh"),
    }

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
                measure_kind, unit_code = energy_type_to_measure[et]
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

    print(f"      building 写入: {len(building_id_map)}")
    print(f"      point 写入:    {len(point_id_map)}")

    # ── 5. 灌 staging_reading ────────────────────────────
    print("[5/6] 灌 ingest.staging_reading (从 readings_2017.csv) ...")
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
            if inserted % 50000 == 0:
                print(f"      已生成 {inserted} 行 ...")
    buf.seek(0)
    cur.copy_from(buf, "staging_reading", sep="\t", null="",
                  columns=("batch_id", "row_no", "site_code", "building_code",
                           "point_code", "ts_text", "value_text", "unit_text", "quality_text"))

    cur.execute("""
        UPDATE ingest.import_batch
        SET row_count_total = %s, row_count_success = %s, status = 'SUCCEEDED'
        WHERE id = %s
    """, (inserted, inserted, reading_batch_id))
    print(f"      staging_reading 灌入: {inserted} 行, 跳过: {skipped}")

    # ── 6. 灌 staging_weather ────────────────────────────
    print("[6/6] 灌 ingest.staging_weather (从 weather_2017.csv) ...")
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
        SET row_count_total = %s, row_count_success = %s, status = 'SUCCEEDED'
        WHERE id = %s
    """, (w_row_no, w_row_no, weather_batch_id))
    print(f"      staging_weather 灌入: {w_row_no} 行")

    # ── 7. merge staging_reading -> fact.point_reading ──
    print("[7/8] merge staging_reading -> fact.point_reading ...")
    cur.execute("""
        INSERT INTO fact.point_reading (
            tenant_id, site_id, building_id, point_id, ts, value_num, quality_code, source_batch_id
        )
        SELECT
            ib.tenant_id,
            p.site_id,
            p.building_id,
            p.id,
            s.ts_text::timestamptz,
            s.value_text::double precision,
            COALESCE(NULLIF(s.quality_text, ''), 'GOOD'),
            ib.id
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
    print(f"      point_reading 写入/更新: {reading_merged} 行")

    # ── 8. merge staging_weather -> fact.weather_reading ──
    print("[8/8] merge staging_weather -> fact.weather_reading ...")
    cur.execute("""
        INSERT INTO fact.weather_reading (
            tenant_id, site_id, ts,
            air_temp_c, cloud_cover_pct, dew_temp_c,
            precip_mm, precip_6hr_mm, sea_level_pressure_hpa,
            wind_direction_deg, wind_speed_mps,
            source_batch_id
        )
        SELECT
            sw.tenant_id,
            st.id AS site_id,
            sw.ts_text::timestamptz,
            sw.air_temp_text::double precision,
            sw.cloud_text::double precision,
            sw.dew_temp_text::double precision,
            sw.precip_1hr_text::double precision,
            sw.precip_6hr_text::double precision,
            sw.pressure_text::double precision,
            sw.wind_dir_text::double precision,
            sw.wind_speed_text::double precision,
            ib.id
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
    print(f"      weather_reading 写入/更新: {weather_merged} 行")

    # ── 9. 生成日聚合 mart.building_daily_energy ─────────
    print("[9/9] 生成 mart.building_daily_energy ...")
    cur.execute("""
        INSERT INTO mart.building_daily_energy
            (tenant_id, building_id, energy_type, date,
             total_kwh, max_kwh, min_kwh, avg_kwh, hour_count, eui_kwh_per_m2,
             source_batch_id)
        SELECT
            p.tenant_id,
            p.building_id,
            p.energy_type,
            pr.ts::date AS date,
            SUM(pr.value_num) AS total_kwh,
            MAX(pr.value_num) AS max_kwh,
            MIN(pr.value_num) AS min_kwh,
            AVG(pr.value_num) AS avg_kwh,
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
    print(f"      building_daily_energy 写入: {daily_rows} 行")

    conn.commit()
    cur.close()
    conn.close()

    print()
    print("=" * 60)
    print("全部完成。数据库现状:")
    print(f"  core:        1 tenant, 1 site, {len(building_id_map)} buildings, {len(point_id_map)} points")
    print(f"  fact:        {reading_merged} point_readings, {weather_merged} weather_readings")
    print(f"  mart:        {daily_rows} building_daily_energy 行")
    print("=" * 60)


if __name__ == "__main__":
    main()
