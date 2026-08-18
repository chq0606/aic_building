--建筑能耗分析与节能优化平台 - PostgreSQL 建表脚本
--数据集: BDG2 子集 (Bobcat site, 6 栋建筑, 2017 全年逐小时)
--8 种能源类型: electricity, hotwater, chilledwater, steam, gas, water, irrigation, solar
--拓扑: tenant / site / building / point (无 space 层, BDG2 无房间级数据)
--智能问答: 现代 RAG (pgvector 向量检索 + tsvector BM25 关键词检索 + metadata 过滤, 三路混检 + rerank + 引用审计)


--基础扩展和 schema
SET client_encoding = 'UTF8';            -- Windows 下 psql 用这个读文件编码, 中文注释才不炸

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector, 支撑 knowledge_chunk.embedding

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS fact;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS knowledge;   -- 知识文档与问答会话



--核心字典表 —— 约束能源类型、度量种类、单位的取值

CREATE TABLE core.energy_type (
    energy_type text PRIMARY KEY,
    measure_kind text NOT NULL,
    unit_code   text NOT NULL,
    description text
);

INSERT INTO core.energy_type (energy_type, measure_kind, unit_code, description) VALUES
    ('electricity',   'energy_kwh',         'kWh',         '电 (kWh)'),
    ('hotwater',      'energy_kwh_thermal', 'kWh_thermal', '供暖热水 (kWh 热能)'),
    ('chilledwater',  'energy_kwh_thermal', 'kWh_thermal', '冷冻水 (kWh 热能)'),
    ('steam',         'energy_kwh_thermal', 'kWh_thermal', '蒸汽 (kWh 热能)'),
    ('gas',           'energy_kwh',         'kWh',         '天然气 (kWh)'),
    ('water',         'volume_liter',       'L',           '用水 (升)'),
    ('irrigation',    'volume_liter',       'L',           '灌溉用水 (升)'),
    ('solar',         'energy_kwh',         'kWh',         '太阳能发电 (kWh)');


--核心拓扑表
CREATE TABLE core.tenant (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_code text NOT NULL UNIQUE,
    tenant_name text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.site (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    site_code     text NOT NULL,
    site_name     text NOT NULL,
    timezone      text NOT NULL DEFAULT 'UTC',
    latitude      double precision,
    longitude     double precision,
    climate_zone  text,
    source_dataset text NOT NULL DEFAULT 'bdg2',
    source_ref    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, site_code)
);

CREATE TABLE core.building (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    site_id          uuid NOT NULL REFERENCES core.site(id) ON DELETE CASCADE,
    building_code    text NOT NULL,
    display_name     text NOT NULL,
    source_name      text,               -- BDG2 metadata.csv 中的建筑物原始名称
    building_type    text,               -- 如 Commercial / Education / Office …
    primary_use      text,               -- BDG2 metadata 中的 primaryspaceusage 字段
    sub_use          text,               -- BDG2 metadata 中的 sub_primaryspaceusage 字段
    sqm              numeric(14,2),      -- BDG2 metadata 中的 sqm (平方米)
    sqft             numeric(14,2),      -- BDG2 metadata 中的 sqft (平方英尺)
    floors_count     integer,
    year_built       integer,
    has_room_level_data boolean NOT NULL DEFAULT false,
    source_dataset   text NOT NULL DEFAULT 'bdg2',
    source_ref       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, building_code)
);

CREATE TABLE core.point (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    site_id            uuid NOT NULL REFERENCES core.site(id) ON DELETE CASCADE,
    building_id        uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    point_code         text NOT NULL,
    point_name         text NOT NULL,
    point_kind         text NOT NULL DEFAULT 'METER' CHECK (point_kind IN ('METER','SENSOR','DERIVED')),
    energy_type        text NOT NULL REFERENCES core.energy_type(energy_type),
    measure_kind       text NOT NULL,   -- 由 energy_type 决定，此处冗余以加速查询
    unit_code          text NOT NULL,   -- 由 energy_type 决定，此处冗余
    sample_interval_sec integer NOT NULL DEFAULT 3600,  -- BDG2 逐小时数据
    source_dataset     text NOT NULL DEFAULT 'bdg2',
    source_ref         text,
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, point_code)
);


-- 上传、映射、批处理表

CREATE TABLE ingest.upload_file (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    original_filename text NOT NULL,
    storage_path      text NOT NULL,
    mime_type         text,
    size_bytes        bigint,
    file_sha256       text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, file_sha256)
);

CREATE TABLE ingest.mapping_profile (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    profile_name    text NOT NULL,
    template_type   text NOT NULL CHECK (template_type IN ('SPACE','POINT','ENERGY','WEATHER')),
    timezone        text,
    timestamp_format text,
    mapping_json    jsonb NOT NULL,
    unit_rules      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ingest.import_batch (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    upload_file_id   uuid REFERENCES ingest.upload_file(id) ON DELETE SET NULL,
    mapping_profile_id uuid REFERENCES ingest.mapping_profile(id) ON DELETE SET NULL,
    dataset_source   text NOT NULL DEFAULT 'bdg2',   -- bdg2 / user_csv / user_api
    target_type      text NOT NULL CHECK (target_type IN ('SPACE','POINT','ENERGY','WEATHER')),
    status           text NOT NULL CHECK (status IN ('PENDING','VALIDATING','LOADING','VALIDATED','MERGING','SUCCEEDED','FAILED')),
    row_count_total   integer NOT NULL DEFAULT 0,
    row_count_success integer NOT NULL DEFAULT 0,
    row_count_error   integer NOT NULL DEFAULT 0,
    error_summary    text,
    started_at       timestamptz,
    finished_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- BDG2 宽表转长表后的暂存表（每行 = 一个测点的一个时刻）
CREATE TABLE ingest.staging_reading (
    batch_id      uuid NOT NULL REFERENCES ingest.import_batch(id) ON DELETE CASCADE,
    row_no        bigint NOT NULL,
    site_code     text,
    building_code text,
    point_code    text NOT NULL,
    ts_text       text NOT NULL,
    value_text    text NOT NULL,
    unit_text     text,
    quality_text  text,
    extra         jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (batch_id, row_no)
);

-- BDG2 天气暂存表，字段对齐 weather.csv 实际列
CREATE TABLE ingest.staging_weather (
    batch_id         uuid NOT NULL REFERENCES ingest.import_batch(id) ON DELETE CASCADE,
    row_no           bigint NOT NULL,
    tenant_id        uuid NOT NULL,
    site_code        text NOT NULL,
    ts_text          text NOT NULL,
    air_temp_text    text,
    cloud_text       text,
    dew_temp_text    text,
    precip_1hr_text  text,
    precip_6hr_text  text,
    pressure_text    text,
    wind_dir_text    text,
    wind_speed_text  text,
    PRIMARY KEY (batch_id, row_no)
);

-- 事实表 (按月分区)
CREATE TABLE fact.point_reading (
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    site_id         uuid NOT NULL REFERENCES core.site(id) ON DELETE CASCADE,
    building_id     uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    point_id        uuid NOT NULL REFERENCES core.point(id) ON DELETE CASCADE,
    ts              timestamptz NOT NULL,
    value_num       double precision NOT NULL,
    quality_code    text NOT NULL DEFAULT 'GOOD',
    source_batch_id uuid REFERENCES ingest.import_batch(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (point_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE fact.weather_reading (
    tenant_id            uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    site_id              uuid NOT NULL REFERENCES core.site(id) ON DELETE CASCADE,
    ts                   timestamptz NOT NULL,
    air_temp_c           double precision,
    cloud_cover_pct      double precision,
    dew_temp_c           double precision,
    precip_mm            double precision,
    precip_6hr_mm        double precision,
    sea_level_pressure_hpa double precision,
    wind_direction_deg   double precision,
    wind_speed_mps       double precision,
    source_batch_id      uuid REFERENCES ingest.import_batch(id) ON DELETE SET NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, ts)
) PARTITION BY RANGE (ts);



-- 异常事件表
CREATE TABLE mart.anomaly_event (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    building_id    uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    point_id       uuid REFERENCES core.point(id) ON DELETE SET NULL,
    event_type     text NOT NULL CHECK (
        event_type IN ('SPIKE','DRIFT','PROLONGED_ZERO','MISSING_GAP','SCHEDULE_VIOLATION','BASELINE_DEVIATION')
    ),
    severity       text NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH')),
    metric_code    text NOT NULL,
    start_ts       timestamptz NOT NULL,
    end_ts         timestamptz NOT NULL,
    observed_value double precision,
    baseline_value double precision,
    evidence       jsonb NOT NULL DEFAULT '{}'::jsonb,
    status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open','ack','closed')),
    created_at     timestamptz NOT NULL DEFAULT now()
);



-- 能耗日聚合表 (楼栋 × 能源类型 × 日)
-- 节能分析核心表: EUI、趋势图、日历热力图、排名、异常基线都基于此表
-- 填充方式: 后端脚本 python -m app.aggregate_daily, 跑一次把 2017 全年算完
CREATE TABLE mart.building_daily_energy (
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    building_id     uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    energy_type     text NOT NULL REFERENCES core.energy_type(energy_type),
    date            date NOT NULL,
    total_kwh       numeric(14,3) NOT NULL,
    max_kwh         numeric(14,3),
    min_kwh         numeric(14,3),
    avg_kwh         numeric(14,3),
    hour_count      integer NOT NULL,          -- 当天有效读数数 (0-24), 用于数据质量判断
    eui_kwh_per_m2  numeric(10,3),             -- total_kwh / sqm, 节能核心指标
    source_batch_id uuid REFERENCES ingest.import_batch(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (building_id, energy_type, date)
);

-- 能耗月聚合视图 (基于日聚合表现算, 数据量小, 无需建实体表)
CREATE OR REPLACE VIEW mart.v_building_monthly_energy AS
SELECT
    b.tenant_id,
    b.id AS building_id,
    b.display_name,
    b.primary_use,
    b.sqm,
    d.energy_type,
    DATE_TRUNC('month', d.date) AS month,
    SUM(d.total_kwh)         AS month_total_kwh,
    AVG(d.max_kwh)           AS month_avg_max_kwh,
    AVG(d.min_kwh)           AS month_avg_min_kwh,
    SUM(d.hour_count)        AS month_hour_count,
    ROUND(CAST(SUM(d.total_kwh) / NULLIF(b.sqm, 0) AS numeric), 3) AS month_eui_kwh_per_m2
FROM mart.building_daily_energy d
JOIN core.building b ON b.id = d.building_id
GROUP BY b.tenant_id, b.id, b.display_name, b.primary_use, b.sqm, d.energy_type, DATE_TRUNC('month', d.date);


-- 3D 资产和建模（后续可视化用）
CREATE TABLE core.building_visual_model (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    building_id       uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    model_mode        text NOT NULL CHECK (model_mode IN ('BLOCK','PHOTO_SINGLE','PHOTO_MULTI','MANUAL_GLTF')),
    render_format     text NOT NULL CHECK (render_format IN ('BOX','GLB','GLTF','PLY','SPLAT','SPZ')),
    storage_path      text,
    preview_image_path text,
    length_m          numeric(10,2),
    width_m           numeric(10,2),
    height_m          numeric(10,2),
    floors_count      integer,
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.reconstruction_job (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    building_id         uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    model_mode          text NOT NULL CHECK (model_mode IN ('BLOCK','PHOTO_SINGLE','PHOTO_MULTI')),
    engine              text NOT NULL CHECK (
        engine IN ('MANUAL_BOX','TRIPOSPLAT','PYCOLMAP_GAUSSIAN_SPLATTING')
    ),
    status              text NOT NULL CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
    input_upload_file_id uuid,
    input_photo_count   integer NOT NULL DEFAULT 0,
    output_model_id     uuid REFERENCES core.building_visual_model(id) ON DELETE SET NULL,
    error_message       text,
    started_at          timestamptz,
    finished_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);


-- 知识文档与智能问答 (现代 RAG: 三路混检 + 引用审计)
-- 文档元数据表: 记录上传的 PDF/Word/Markdown 等文档
-- 原始文件存文件系统, source_path 指向; 数据库只存元数据
CREATE TABLE knowledge.document (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    doc_type      text NOT NULL CHECK (doc_type IN ('standard','manual','sop','report','paper','other')),
    title         text NOT NULL,
    source_path   text NOT NULL,            -- 原始文件路径 (文件系统/对象存储)
    file_hash     text NOT NULL,            -- SHA256, 用于去重
    mime_type     text,
    page_count    integer,
    chunk_count   integer NOT NULL DEFAULT 0,
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','parsing','parsed','failed')),
    parse_error   text,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,  -- jurisdiction/effective_date/version/equipment_type 等
    uploaded_at   timestamptz NOT NULL DEFAULT now(),
    parsed_at     timestamptz,
    UNIQUE (tenant_id, file_hash)
);

-- 文档切块表: 每行 = 一个 chunk 的文本 + 向量 + 关键词索引
-- 三路混检: embedding (向量) + search_vector (BM25) + metadata (jsonb 过滤)
CREATE TABLE knowledge.chunk (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id          uuid NOT NULL REFERENCES knowledge.document(id) ON DELETE CASCADE,
    chunk_index     integer NOT NULL,
    chunk_text      text NOT NULL,
    page_no         integer,
    section_title   text,
    embedding       vector(1024),           -- pgvector, 对齐 BGE-large-zh-v1.5 / bge-m3 的 1024 维
    search_vector   tsvector,               -- BM25 关键词检索, 专业术语比向量准
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (doc_id, chunk_index)
);

-- tsvector 自动生成: chunk_text 变化时同步更新 (中文用 simple 分词, 后端可换 jieba/pg_jieba)
CREATE OR REPLACE FUNCTION knowledge.chunk_tsv_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('simple', NEW.chunk_text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_chunk_tsv
    BEFORE INSERT OR UPDATE OF chunk_text ON knowledge.chunk
    FOR EACH ROW EXECUTE FUNCTION knowledge.chunk_tsv_update();


-- 智能问答会话表
CREATE TABLE knowledge.assistant_session (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    user_id     text,                       -- 后端用户系统对接, 一期可空
    title       text,                       -- 会话标题 (首条问题自动生成或用户命名)
    context     jsonb NOT NULL DEFAULT '{}'::jsonb,  -- 当前上下文: site/building/time_range/metric 等
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 智能问答消息表: 每条 user/assistant/tool 消息都落库, 支撑引用审计
CREATE TABLE knowledge.assistant_message (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            uuid NOT NULL REFERENCES knowledge.assistant_session(id) ON DELETE CASCADE,
    role                  text NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content               text NOT NULL,
    tool_calls            jsonb NOT NULL DEFAULT '[]'::jsonb,    -- 执行链: 调用了哪些工具/API
    citations             jsonb NOT NULL DEFAULT '[]'::jsonb,    -- 引用证据: 文档片段/数据卡片
    referenced_chunk_ids  uuid[] NOT NULL DEFAULT '{}',          -- 引用的 chunk_id 列表
    referenced_point_ids  uuid[] NOT NULL DEFAULT '{}',          -- 引用的测点 id 列表
    tokens_used           integer,
    latency_ms            integer,
    created_at            timestamptz NOT NULL DEFAULT now()
);


-- 分区 — 覆盖 BDG2 时间范围 2016~2017 + 缓冲至 2030, 方便后续客户数据接入
DO $$
DECLARE
    d date := DATE '2016-01-01';
BEGIN
    WHILE d < DATE '2031-01-01' LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS fact.point_reading_%s PARTITION OF fact.point_reading
             FOR VALUES FROM (%L) TO (%L);',
            to_char(d, 'YYYY_MM'), d, (d + INTERVAL '1 month')::date
        );

        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS fact.weather_reading_%s PARTITION OF fact.weather_reading
             FOR VALUES FROM (%L) TO (%L);',
            to_char(d, 'YYYY_MM'), d, (d + INTERVAL '1 month')::date
        );

        d := (d + INTERVAL '1 month')::date;
    END LOOP;
END $$;



--  索引
CREATE INDEX idx_point_building_energy_kind
    ON core.point(building_id, energy_type, measure_kind);

CREATE INDEX idx_point_site
    ON core.point(site_id);

CREATE INDEX idx_visual_model_building_active
    ON core.building_visual_model(building_id, is_active);

CREATE INDEX idx_fact_point_point_ts_desc
    ON fact.point_reading(point_id, ts DESC);

CREATE INDEX idx_fact_point_ts_brin
    ON fact.point_reading USING BRIN(ts);

CREATE INDEX idx_fact_point_site_ts
    ON fact.point_reading(site_id, ts DESC);

CREATE INDEX idx_fact_weather_site_ts_desc
    ON fact.weather_reading(site_id, ts DESC);

CREATE INDEX idx_fact_weather_ts_brin
    ON fact.weather_reading USING BRIN(ts);

-- 日聚合表索引: 支撑建筑排名、趋势图、月聚合视图
CREATE INDEX idx_daily_building_date
    ON mart.building_daily_energy(building_id, date DESC);

CREATE INDEX idx_daily_energy_type_date
    ON mart.building_daily_energy(energy_type, date DESC);

CREATE INDEX idx_daily_building_type_date
    ON mart.building_daily_energy(building_id, energy_type, date DESC);

CREATE INDEX idx_anomaly_building_time
    ON mart.anomaly_event(building_id, start_ts DESC);

CREATE INDEX idx_anomaly_open
    ON mart.anomaly_event(building_id, severity, start_ts DESC)
    WHERE status = 'open';

-- 知识库索引: 向量检索 (IVFFlat, 适合中等规模) + BM25 关键词 (GIN)
-- 向量索引用 cosine 距离算子 <=>, lists=100 适合 10 万级 chunk, 后续可调
CREATE INDEX idx_chunk_embedding
    ON knowledge.chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX idx_chunk_search_vector
    ON knowledge.chunk USING gin(search_vector);

CREATE INDEX idx_chunk_doc_id
    ON knowledge.chunk(doc_id);

CREATE INDEX idx_document_tenant_status
    ON knowledge.document(tenant_id, status);

-- 问答会话索引
CREATE INDEX idx_assistant_session_tenant
    ON knowledge.assistant_session(tenant_id, created_at DESC);

CREATE INDEX idx_assistant_message_session
    ON knowledge.assistant_message(session_id, created_at DESC);

-- 加载向量索引前需要有一定数据, ivfflat 建议在数据导入后重建索引:
-- DROP INDEX IF EXISTS knowledge.idx_chunk_embedding;
-- CREATE INDEX idx_chunk_embedding ON knowledge.chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);


-- 派生视图：按 sample_interval 自动换算 interval 能耗 (kWh)
CREATE OR REPLACE VIEW mart.v_interval_energy AS
SELECT
    pr.tenant_id,
    pr.site_id,
    pr.building_id,
    pr.point_id,
    pr.ts,
    p.energy_type,
    p.measure_kind,
    p.unit_code,
    pr.value_num AS raw_value_num,
    CASE
        WHEN p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal')
            THEN pr.value_num
        ELSE NULL
    END AS interval_energy_kwh
FROM fact.point_reading pr
JOIN core.point p ON p.id = pr.point_id;


-- 导入流程：
-- 数据导入 (CSV -> staging -> fact) 和日聚合生成已在 code/seed_bdg2.py 脚本中完成，
-- 不在此 SQL 内。建表完成后运行:  python code/seed_bdg2.py
