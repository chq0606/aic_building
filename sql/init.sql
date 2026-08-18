-- =============================================================
-- init.sql - 自动生成, 不要手动改
-- 由 tools/merge_sql.py 合并 12 个 SQL 文件得到, 给 docker postgres
-- 容器的 /docker-entrypoint-initdb.d/ 用 (首次启动自动执行)
-- =============================================================

-- >>>>>> 开始: postgresql_bdg2.sql <<<<<<
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


-- >>>>>>> 结束: postgresql_bdg2.sql <<<<<<<

-- >>>>>> 开始: auth_bdg2.sql <<<<<<
-- ============================================================
-- 认证模块建表 SQL（Step 02 注册 + 登录）
-- 项目：建筑能耗分析与节能优化平台
-- 说明：注册 + 登录 + JWT，一人一租户，不做 RBAC
-- 依赖：postgresql_bdg2.sql 已执行（core.tenant 已存在）
--
-- 本文件只建表，不插数据。
-- 注册用户的账号由后端 Step 02 注册 API 创建。
-- demo 用户由后端 Step 03 seed 时顺便创建（demo / demo123）。
--
-- 类型对齐：所有 id 字段统一用 uuid（与 postgresql_bdg2.sql 一致）
-- ============================================================

SET client_encoding = 'UTF8';

-- ── 1. core."user" 用户表 ───────────────────────────────────
-- 一人一租户：每个用户绑一个 tenant，注册时后端自动创建 tenant
-- id / tenant_id 都是 uuid，对齐 postgresql_bdg2.sql 的风格
DROP TABLE IF EXISTS core."user" CASCADE;

CREATE TABLE core."user" (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    username        text NOT NULL UNIQUE,        -- 全局唯一（跨所有租户）
    password_hash   text NOT NULL,               -- bcrypt 哈希
    display_name    text,
    email           text,
    is_active       boolean NOT NULL DEFAULT true,
    is_demo         boolean NOT NULL DEFAULT false,  -- demo 用户标记，只读
    last_login_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_user_tenant ON core."user" (tenant_id);
CREATE INDEX idx_user_username ON core."user" (username);

COMMENT ON TABLE core."user" IS '系统用户表，一人一租户，is_demo 标记 demo 只读账号';
COMMENT ON COLUMN core."user".password_hash IS 'bcrypt 哈希，不存明文';
COMMENT ON COLUMN core."user".is_demo IS 'demo 用户标记，业务 API 检测到则禁止写操作';

-- ── 2. updated_at 自动更新触发器 ────────────────────────────
CREATE OR REPLACE FUNCTION core.touch_user_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_user_updated_at ON core."user";
CREATE TRIGGER trg_user_updated_at
    BEFORE UPDATE ON core."user"
    FOR EACH ROW
    EXECUTE FUNCTION core.touch_user_updated_at();

-- ============================================================
-- 执行完毕
-- 验证：SELECT COUNT(*) FROM core."user";  -- 应返回 0
--
-- 后端 Step 02 注册 API 逻辑：
--   1. INSERT core.tenant (tenant_code, tenant_name) VALUES ('user_'+username, username)
--   2. INSERT core."user" (tenant_id, username, password_hash, ...) VALUES (新tenant.id, ...)
--   3. 返回 JWT
--
-- 后端 Step 03 seed 逻辑：
--   1. 检测 core."user" 是否存在 username='demo'
--   2. 不存在则 INSERT，is_demo=true，password_hash = bcrypt('demo123')
-- ============================================================


-- >>>>>>> 结束: auth_bdg2.sql <<<<<<<

-- >>>>>> 开始: upload_bdg2.sql <<<<<<
-- ============================================================
-- 客户数据上传模块建表 SQL（Step 04 上传与字段映射）
-- 项目：建筑能耗分析与节能优化平台
-- 说明：新建 ingest.upload_session 表，记上传会话状态机
-- 依赖：postgresql_bdg2.sql 已执行（ingest.upload_file / mapping_profile 已存在）
--
-- 设计取舍：
-- - upload_session 和 import_batch 分开。upload_session 记上传→映射→校验→commit
--   的生命周期；import_batch 记 commit 后 staging → fact merge 的生命周期。
--   两个阶段职责不同，分开记更清晰。
-- - session 的 status 走状态机：UPLOADED → MAPPED → VALIDATED → COMMITTED
--   任何一步失败置 FAILED，error_summary 记原因。
-- - mapping_json 存当前会话使用的列映射（从 mapping_profile 复制一份过来，
--   用户调整后保存到这里，不影响原 profile）。
-- ============================================================

SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS ingest.upload_session (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    upload_file_id  uuid NOT NULL REFERENCES ingest.upload_file(id) ON DELETE CASCADE,
    mapping_profile_id uuid REFERENCES ingest.mapping_profile(id) ON DELETE SET NULL,

    -- 文件类型：POINT（读数）/ WEATHER（天气）/ BUILDING（建筑元数据）
    -- 对应三层级方案：A/B 单文件通常是 POINT，C 多文件各自一个 session
    target_type     text NOT NULL CHECK (target_type IN ('POINT','WEATHER','BUILDING')),

    -- 状态机：UPLOADED（刚上传）→ MAPPED（已保存映射）→ VALIDATED（校验通过）→ COMMITTED（已进 staging）
    -- 任何一步失败置 FAILED
    status          text NOT NULL DEFAULT 'UPLOADED'
                    CHECK (status IN ('UPLOADED','MAPPED','VALIDATED','COMMITTED','FAILED')),

    -- 当前会话使用的列映射。从 mapping_profile 复制过来，用户可调整
    -- 结构：{timestamp_col, building_col, energy_col, value_col, unit_col,
    --        wide_melt: {id_cols: [...], value_cols: [...], parse_rules: {...}}}
    mapping_json    jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- 时区 + 时间格式（从 mapping_profile 带过来，用户可改）
    timezone        text,
    timestamp_format text,

    -- 校验结果摘要
    row_count_total   integer NOT NULL DEFAULT 0,
    row_count_valid   integer NOT NULL DEFAULT 0,
    row_count_error   integer NOT NULL DEFAULT 0,
    error_summary    text,

    -- commit 后生成的 import_batch id（关联到 Step 05 的 merge 流程）
    committed_batch_id uuid REFERENCES ingest.import_batch(id) ON DELETE SET NULL,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_upload_session_tenant ON ingest.upload_session (tenant_id);
CREATE INDEX idx_upload_session_status ON ingest.upload_session (tenant_id, status);

-- updated_at 自动更新触发器
CREATE OR REPLACE FUNCTION ingest.touch_upload_session_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_upload_session_updated_at ON ingest.upload_session;
CREATE TRIGGER trg_upload_session_updated_at
    BEFORE UPDATE ON ingest.upload_session
    FOR EACH ROW
    EXECUTE FUNCTION ingest.touch_upload_session_updated_at();

COMMENT ON TABLE ingest.upload_session IS '上传会话状态机：UPLOADED→MAPPED→VALIDATED→COMMITTED，记每个上传文件从上传到进 staging 的生命周期';
COMMENT ON COLUMN ingest.upload_session.mapping_json IS '当前会话的列映射，从 mapping_profile 复制过来可调整';
COMMENT ON COLUMN ingest.upload_session.committed_batch_id IS 'commit 后生成的 import_batch id，Step 05 merge 用这个 id';

-- ============================================================
-- 执行完毕
-- 验证：SELECT COUNT(*) FROM ingest.upload_session;  -- 应返回 0
-- ============================================================


-- >>>>>>> 结束: upload_bdg2.sql <<<<<<<

-- >>>>>> 开始: step05_import.sql <<<<<<
-- ============================================================
-- Step 05 schema 补丁：import_batch 状态机扩展
-- 项目：建筑能耗分析与节能优化平台
-- 说明：step 05 引入 staging -> fact merge 流程，import_batch.status
--       增加 MERGING 中间态。VALIDATED 沿用提示词设计但实际不写入
--       （step 04 的 validate 在 upload_session 里做了，import_batch
--       只需走 LOADING -> MERGING -> SUCCEEDED/FAILED）
-- 依赖：postgresql_bdg2.sql 已执行
-- ============================================================

SET client_encoding = 'UTF8';

-- 状态枚举扩展：PENDING/VALIDATING/LOADING/SUCCEEDED/FAILED 保留兼容老数据
-- 新增 VALIDATED/MERGING 给 step 05 用
ALTER TABLE ingest.import_batch DROP CONSTRAINT IF EXISTS import_batch_status_check;
ALTER TABLE ingest.import_batch ADD CONSTRAINT import_batch_status_check
    CHECK (status IN ('PENDING','VALIDATING','LOADING','VALIDATED','MERGING','SUCCEEDED','FAILED'));

COMMENT ON COLUMN ingest.import_batch.status IS
    'LOADED=staging已灌(实际用LOADING); MERGING=step05后台merge进行中; SUCCEEDED=merge到fact完成; FAILED=merge出错';

-- ============================================================
-- 验证：SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='import_batch_status_check';
-- ============================================================


-- >>>>>>> 结束: step05_import.sql <<<<<<<

-- >>>>>> 开始: step08_anomaly.sql <<<<<<
-- Step 08 异常检测服务的 anomaly_event 表索引补丁。
-- 主表结构在 postgresql_bdg2.sql 已建好,本文件只补索引:
-- 1. 两个 partial unique index 防止同一 building/point/event_type/start_ts 重复写入
--    (决策点 #5: UUID 主键 + UNIQUE 复合键防重复。point_id 可空,用 partial index
--     分别覆盖 point-level 和 building-level 场景。PG 默认 NULL 不参与 UNIQUE 比较,
--     不分拆的话 building-level 检测的 NULL point_id 会全部"视为不重复",无法防重)
-- 2. 按 event_type + 时间范围查询的普通索引(Step 18 AI 助手按事件类型检索用)
-- 3. 按 tenant_id + 时间范围查询的索引(园区概览按租户汇总用)
--
-- 幂等写法,可重复执行。

SET client_encoding = 'UTF8';

-- point-level 检测的 unique 约束(SPIKE/DRIFT/PROLONGED_ZERO/MISSING_GAP/SCHEDULE_VIOLATION)
CREATE UNIQUE INDEX IF NOT EXISTS uq_anomaly_event_point_level
    ON mart.anomaly_event(tenant_id, building_id, point_id, event_type, start_ts)
    WHERE point_id IS NOT NULL;

-- building-level 检测的 unique 约束(BASELINE_DEVIATION)
CREATE UNIQUE INDEX IF NOT EXISTS uq_anomaly_event_building_level
    ON mart.anomaly_event(tenant_id, building_id, event_type, start_ts)
    WHERE point_id IS NULL;

-- AI 助手按事件类型检索的索引
CREATE INDEX IF NOT EXISTS idx_anomaly_event_type_time
    ON mart.anomaly_event(event_type, start_ts DESC);

-- 园区概览按租户+时间范围汇总的索引
CREATE INDEX IF NOT EXISTS idx_anomaly_event_tenant_time
    ON mart.anomaly_event(tenant_id, start_ts DESC);


-- >>>>>>> 结束: step08_anomaly.sql <<<<<<<

-- >>>>>> 开始: step09_knowledge.sql <<<<<<
-- Step 09 知识库 schema 调整:
--   1. 给 knowledge.chunk 加 chunk_text_tokenized (jieba 分词后的文本)
--   2. 改 chunk_tsv_update trigger: 用 chunk_text_tokenized 算 tsvector,
--      没设的话回退到 chunk_text (兼容旧数据)
--
-- 为什么这么干:
--   原 trigger to_tsvector('simple', chunk_text) 对中文不分词, BM25 形同虚设。
--   PG 服务端中文分词要装 zhparser/pg_jieba 扩展, 需要超级用户 + Windows
--   下编译麻烦。Python jieba 预分词更简单, 写入时把分词后文本塞
--   chunk_text_tokenized, trigger 把这个字段转 tsvector, simple 配合按
--   空格切分刚好对上 jieba 的空格分隔。chunk_text 保留原文, 给前端展示。

ALTER TABLE knowledge.chunk
    ADD COLUMN IF NOT EXISTS chunk_text_tokenized text;

DROP TRIGGER IF EXISTS trg_chunk_tsv ON knowledge.chunk;

CREATE OR REPLACE FUNCTION knowledge.chunk_tsv_update() RETURNS trigger AS $$
BEGIN
    -- 优先用 jieba 分词后的文本; 没设 (NULL 或空) 回退到 chunk_text
    IF NEW.chunk_text_tokenized IS NOT NULL AND NEW.chunk_text_tokenized <> '' THEN
        NEW.search_vector := to_tsvector('simple', NEW.chunk_text_tokenized);
    ELSE
        NEW.search_vector := to_tsvector('simple', NEW.chunk_text);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_chunk_tsv
    BEFORE INSERT OR UPDATE OF chunk_text, chunk_text_tokenized ON knowledge.chunk
    FOR EACH ROW EXECUTE FUNCTION knowledge.chunk_tsv_update();


-- >>>>>>> 结束: step09_knowledge.sql <<<<<<<

-- >>>>>> 开始: step10_retrieval.sql <<<<<<
-- Step 10 检索服务 schema 补丁: 给 knowledge.chunk 加 section_path 字段
--
-- section_path 存完整条款路径, 如 "3 -> 3.1 -> 3.1.1", section_title 保留
-- 当前条款号 "3.1.1"。两个字段职责分开: section_title 给前端按条款号
-- 定位, section_path 给前端按层级树展示。
--
-- 为什么要这个字段: Step 09 原计划只存 section_title (当前条款号), 检索
-- 返回的 chunk 没有"我属于哪一章哪一节"的完整上下文, 前端展示只能看到
-- "3.1.1 一般规定"看不到所属的"3 设计要求"。Step 18 AI 抽屉引用国标
-- 条款时也要带完整路径给 LLM 当上下文。补完整路径, 不重跑检索流程。
--
-- 不加索引: section_path 不参与 SQL 检索 (向量/关键词都不查它), 只在
-- 检索结果里返回给前端展示。建索引浪费空间和写入开销。
--
-- chunker.py 维护条款栈: 遇 "1" 栈=[1], "1.1" 栈=[1, 1.1], "2" 栈=[2]。
-- 跨页保持栈状态 (同一条款跨页时栈不变), 异常结构 (如 1.1 后直接
-- 1.1.1.1.1) 兜底用完整新条款号。

ALTER TABLE knowledge.chunk
    ADD COLUMN IF NOT EXISTS section_path text;


-- >>>>>>> 结束: step10_retrieval.sql <<<<<<<

-- >>>>>> 开始: step11_visual.sql <<<<<<
-- Step 11 体块模式后端 schema 补丁: 给 core.building_visual_model 加
-- position_x / position_y 两列。
--
-- 为什么需要这两个字段: 提示词1.txt Step 11 的 POST /buildings/{id}/visual-models/block
-- body 明确要求带 position_x / position_y, 是园区内的相对坐标 (米)。原表
-- (postgresql_bdg2.sql:278-292) 只有 length_m / width_m / height_m 三个尺寸字段,
-- 没有任何字段表达"楼在园区哪个位置"。scene API 一次返回所有 building 的
-- position 给前端 CesiumJS 渲染, 没这个字段没法定位。
--
-- 为什么 nullable: 旧的 visual_model 记录没 position 数据, backfill 没意义。
-- 自动估算的体块 (building 没设过 visual_model) 也允许 position 为空, 前端
-- 布局算法兜底 (PackedBoxLayout 或网格布局)。手动提交体块的 user 必填 position,
-- 在 Pydantic schema 层校验 (BlockModelRequest.position_x/y 虽然声明为 Optional
-- 但 API 层会提示用户填)。
--
-- 为什么 numeric(10,2): 园区尺度一般 < 1km (100000m), 整数部分 5 位够用, 精度
-- 到 0.01m (1cm) 远超 CesiumJS 渲染精度需求。跟 length_m / width_m / height_m
-- 类型一致。
--
-- 不加索引: scene API 按 building_id + is_active 过滤 (已有
-- idx_visual_model_building_active 索引覆盖), position 不参与 SQL 查询条件,
-- 建索引浪费空间。

ALTER TABLE core.building_visual_model
    ADD COLUMN IF NOT EXISTS position_x numeric(10,2),
    ADD COLUMN IF NOT EXISTS position_y numeric(10,2);


-- >>>>>>> 结束: step11_visual.sql <<<<<<<

-- >>>>>> 开始: step12_reconstruction.sql <<<<<<
-- Step 12: 重建任务表扩展补丁
--
-- 给 core.reconstruction_job 表加 8 列:
--   retry_count        重试计数, worker 自动重试用
--   last_retry_at      上次重试时间, 隔 60s 防止瞬间重试
--   input_length_m     输入参数: 建筑长度, 用于尺度校准
--   input_width_m      输入参数: 建筑宽度
--   input_height_m     输入参数: 建筑总高
--   input_floors_count 输入参数: 楼层数
--   input_position_x   输入参数: 园区内 X 坐标, 可空
--   input_position_y   输入参数: 园区内 Y 坐标, 可空
--
-- 输入参数存 job 表的原因: worker 进程跟 API 进程独立, 用 DB 传参
-- 比从其他表 join 拿简单 (photo 已用 input_upload_file_id 字段存 uuid, 不写 FK)。
--
-- 加部分索引只为 PENDING 行: 轮询查询走 WHERE status='PENDING'
-- AND last_retry_at + interval '60s' < now(), 索引覆盖。SUCCEEDED/FAILED/
-- RUNNING 行不进索引, 不占空间。

ALTER TABLE core.reconstruction_job
    ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_retry_at timestamptz,
    ADD COLUMN IF NOT EXISTS input_length_m numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_width_m numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_height_m numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_floors_count integer,
    ADD COLUMN IF NOT EXISTS input_position_x numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_position_y numeric(10,2);

CREATE INDEX IF NOT EXISTS idx_recon_job_pending
    ON core.reconstruction_job (status, last_retry_at)
    WHERE status = 'PENDING';


-- >>>>>>> 结束: step12_reconstruction.sql <<<<<<<

-- >>>>>> 开始: prediction_bdg2.sql <<<<<<
-- ============================================================
-- 能耗预测模块建表 SQL（Step 19 能耗预测服务）
-- 项目：建筑能耗分析与节能优化平台
-- 说明：单楼能耗预测，Prophet 主力 + LSTM demo 对比
-- 依赖：postgresql_bdg2.sql 已执行（core.tenant / core.building 已存在）
--
-- 本文件只建表，不插数据。
-- 预测任务由后端 Step 19 的 prediction API 创建，prediction_worker 消费执行。
--
-- 类型对齐：所有 id 字段统一用 uuid（与 postgresql_bdg2.sql 一致）
-- ============================================================

SET client_encoding = 'UTF8';

-- ── 1. mart.prediction_job 预测任务表 ───────────────────────
-- 异步任务队列：API 创建 PENDING 任务，worker 拉取执行，写回结果
DROP TABLE IF EXISTS mart.prediction_job CASCADE;

CREATE TABLE mart.prediction_job (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    building_id     uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    model_type      text NOT NULL CHECK (model_type IN ('prophet','lstm','linear')),
    horizon_days    integer NOT NULL CHECK (horizon_days BETWEEN 1 AND 90),
    status          text NOT NULL CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
    mape            numeric(6,2),               -- 平均绝对百分比误差，评估精度
    result_jsonb    jsonb,                      -- 预测序列 + 置信区间
    started_at      timestamptz,
    finished_at     timestamptz,
    error_message   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 按建筑查最近预测：building_id + created_at DESC
CREATE INDEX idx_prediction_building ON mart.prediction_job (building_id, created_at DESC);
-- worker 轮询用：status + created_at
CREATE INDEX idx_prediction_status ON mart.prediction_job (status, created_at);
-- 租户隔离查询
CREATE INDEX idx_prediction_tenant ON mart.prediction_job (tenant_id, created_at DESC);

COMMENT ON TABLE mart.prediction_job IS '能耗预测任务表，Prophet 主力 + LSTM demo';
COMMENT ON COLUMN mart.prediction_job.model_type IS 'prophet=Facebook Prophet, lstm=PyTorch LSTM demo, linear=线性回归基线';
COMMENT ON COLUMN mart.prediction_job.horizon_days IS '预测天数，1-90 天';
COMMENT ON COLUMN mart.prediction_job.mape IS '平均绝对百分比误差，<20% 算可用';
COMMENT ON COLUMN mart.prediction_job.result_jsonb IS '{history:[{date,value}], forecast:[{date,yhat,yhat_lower,yhat_upper}], mape:数值, model_type:类型}';

-- ── 2. updated_at 自动更新触发器 ────────────────────────────
CREATE OR REPLACE FUNCTION mart.touch_prediction_job_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prediction_job_updated_at ON mart.prediction_job;
CREATE TRIGGER trg_prediction_job_updated_at
    BEFORE UPDATE ON mart.prediction_job
    FOR EACH ROW
    EXECUTE FUNCTION mart.touch_prediction_job_updated_at();

-- ============================================================
-- 执行完毕
-- 验证：SELECT COUNT(*) FROM mart.prediction_job;  -- 应返回 0
--
-- 后端 Step 19 prediction_worker 逻辑：
--   1. SELECT * FROM mart.prediction_job WHERE status='PENDING'
--      ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
--   2. UPDATE status='RUNNING', started_at=now()
--   3. 调 prediction_service.predict_prophet / predict_lstm
--   4. 成功：UPDATE status='SUCCEEDED', mape=..., result_jsonb=..., finished_at=now()
--      失败：UPDATE status='FAILED', error_message=..., finished_at=now()
-- ============================================================


-- >>>>>>> 结束: prediction_bdg2.sql <<<<<<<

-- >>>>>> 开始: step17_settings.sql <<<<<<
-- =============================================================================
-- Step 17: 系统设置页 - 用户级配置表
-- =============================================================================
-- 背景:
--   开源自部署, 每个使用者跑一个本地实例。GLM API Key 用户自己配,
--   不能写死在 .env 里 (那会成为全局共享值, 跟"每人配自己的 Key"需求冲突)。
--   一人一租户的前提下, 把 GLM Key 按 user_id 存最合理:
--     - demo 体验账号也能配 (demo 用户试用 AI 助手时需要自己的 Key)
--     - 普通注册用户各配各的, 互不干扰
--
-- 不存 BGE 模型路径:
--   embedding_service 启动时 singleton 加载 BGE 模型, 整个进程共用一个实例,
--   不能按用户切换。BGE 路径仍走 .env (settings.bge_model_path), 前端只读展示。
--
-- 加密策略:
--   glm_api_key_encrypted 存 AES-GCM 密文 (base64 编码) + nonce 拼接。
--   密钥从 settings.jwt_secret 派生 (PBKDF2-HMAC-SHA256, 32 字节)。
--   jwt_secret 本身不入库, 派生函数在 settings_service.py。
--
-- hint 字段:
--   存脱敏提示 (如 "sk-***ab12"), 前端展示用。明文不入库, 但脱敏值可读,
--   让用户在 UI 上能看到"已配置过, 末 4 位是 ab12", 不需要每次解密判断。
--
-- 字段说明:
--   user_id:                  主键, 同时外键到 core."user".id, 用户删了配置级联删
--   glm_api_key_encrypted:    AES-GCM 密文 + nonce, base64 编码。NULL = 未配置
--   glm_api_key_hint:         脱敏提示, 用于前端展示。NULL = 未配置
--   updated_at:               最后修改时间, 前端展示"上次配置时间"
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.user_settings (
    user_id                  uuid PRIMARY KEY REFERENCES core."user"(id) ON DELETE CASCADE,
    glm_api_key_encrypted    text,
    glm_api_key_hint         text,
    updated_at               timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.user_settings IS '用户级配置 (Step 17): GLM API Key 加密存储, 按 user_id 隔离';
COMMENT ON COLUMN core.user_settings.glm_api_key_encrypted IS 'AES-GCM 密文 (base64), 用 jwt_secret 派生密钥加密';
COMMENT ON COLUMN core.user_settings.glm_api_key_hint IS '脱敏提示 (如 sk-***ab12), 前端展示用';


-- >>>>>>> 结束: step17_settings.sql <<<<<<<

-- >>>>>> 开始: step18_assistant.sql <<<<<<
-- ============================================================================
-- Step 18: AI 抽屉 - assistant_message 加 optimization_plan 字段
-- ----------------------------------------------------------------------------
-- 原表在 postgresql_bdg2.sql 里建过 (role/content/tool_calls/citations/...)。
-- 节能优化场景 LLM 输出四段结构化 JSON (summary/problems/measures/priorities),
-- 跟自然语言总结 (content) 是同一条 assistant 消息的两个层面, 拆字段存
-- 比 hack 进 citations 干净, 也不污染原 citations 语义 (citations 仍专存
-- 文档片段/数据卡片引用)。
--
-- 字段为 jsonb, 节能优化场景写入, 其他场景为 NULL。前端 GET /optimization-plan
-- 时返这个字段的值, 不存在返 404 表示"这条消息没有结构化方案"。
-- ============================================================================

ALTER TABLE knowledge.assistant_message
    ADD COLUMN IF NOT EXISTS optimization_plan jsonb;

COMMENT ON COLUMN knowledge.assistant_message.optimization_plan IS
    '节能优化场景的四段结构化输出: {summary, problems[], measures[], priorities[]}。其他场景为 NULL。';


-- >>>>>>> 结束: step18_assistant.sql <<<<<<<
