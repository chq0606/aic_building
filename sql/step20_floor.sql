-- ============================================================================
-- Step 20: 楼层级深化分析 - schema 改动 (一键迁移)
-- ----------------------------------------------------------------------------
-- 这个文件包含两部分, 一条命令跑完:
--   psql -d aic_building -f step20_floor.sql
--
-- A. 补漏: step13 / step12_tiles / step19 三个增量 SQL 没被 merge_sql.py
--    合并进 init.sql (merge_sql.py 的 SQL_FILES 列表过期了), 这里补上。
--    语法幂等, 跑过再跑不会报错。
--
-- B. Step 20 主体: 楼层级深化分析的新 schema
--    - core.floor 楼层实体表 (楼层升级为一等实体, 不再只是 building.floors_count 整数)
--    - core.point 加 floor_id 外键 (楼栋级 METER floor_id=NULL, 楼层级 SENSOR 指向 floor)
--    - mart.floor_daily_energy 楼层×能源×日聚合 (含 EUI, 前端图表走这张表)
--    - mart.point_status_snapshot 设备状态物化 (ONLINE/OFFLINE/FAULT/STALE)
--
-- BDG2 没有楼层级能耗数据, 楼层 point 的 source_dataset 标 'synthetic_floor',
-- 后续真实导入楼层数据时按这个标记区分, 一键清理虚拟数据不影响真实楼栋总表。
-- ============================================================================


-- ============================================================================
-- A. 补漏: step13 + step12_tiles + step19 (幂等, 已跑过再跑无副作用)
-- ============================================================================

-- Step 13: core.building_visual_model 加 yaw_deg (水平旋转角度, 度, 0-360)
-- 前端 BuildingSplat 渲染时把 yaw_deg 转弧度插到 ENU 平移和 tile 缩放之间,
-- 让 splat 绕"上"方向 (ENU Z 轴) 旋转。BuildingBlock 灰盒不旋转 (axis-aligned)。
ALTER TABLE core.building_visual_model
    ADD COLUMN IF NOT EXISTS yaw_deg NUMERIC(5,2) DEFAULT 0 NOT NULL;

-- Step 12 (后续补丁): core.building_visual_model 加 tiles_path
-- 3D Tiles tileset.json 的绝对路径。TripoSplat worker 生成 .ply 后调
-- 3dgs-ply-3dtiles-converter 转 3D Tiles (Cesium 1.143+ 原生支持 KHR_gaussian_splatting
-- GLB tile), 输出目录 {job_dir}/tiles/。tiles_path=NULL 时前端降级走 .ply 静态点云。
ALTER TABLE core.building_visual_model
    ADD COLUMN IF NOT EXISTS tiles_path text;

-- Step 19: knowledge.document 加 embedding 状态 + embedded_chunk_count
-- 原 status CHECK 只允许 pending/parsing/parsed/failed, embed_document 跑时
-- 不改 status, 前端看不到"向量化中/已完成"。加 'embedding' 中间态 + chunk 计数。
ALTER TABLE knowledge.document
    DROP CONSTRAINT IF EXISTS document_status_check;
ALTER TABLE knowledge.document
    ADD CONSTRAINT document_status_check
    CHECK (status IN ('pending','parsing','parsed','embedding','failed'));
ALTER TABLE knowledge.document
    ADD COLUMN IF NOT EXISTS embedded_chunk_count integer NOT NULL DEFAULT 0;


-- ============================================================================
-- B. Step 20 主体: 楼层级深化分析 schema
-- ============================================================================


-- ----------------------------------------------------------------------------
-- B1. core.floor 楼层实体表
--
-- 存什么: 一栋楼的一层。floor_number 从 1 起 (1=底层, N=顶层), floor_name 是
-- 前端显示名 ("3F 教室"), floor_type 决定能源分布规则 (gas 只在 LAB/MECHANICAL/
-- SPORTS 层, solar 只在 is_rooftop=true 的顶层), area_sqm 算 EUI 用, is_rooftop
-- 标记顶层 (solar 落位依据, 不用 floor_number=max 判断, 改楼层数时约束不失效)。
--
-- 为什么 floor_type 用 CHECK 枚举不用 text:
--   枚举把 allowed 值固化在 schema 里, seed 和 API 都能挡住非法值。后端 Python
--   侧的 floor_type 跟这个枚举对齐, 前端 TS 也照抄一份。
--
-- 为什么 metadata jsonb 存房间布局:
--   平面透视图 (IsometricFloor.vue) 要按房间网格坐标渲染, 房间布局跟楼层用途
--   绑定 (LOBBY 一进大门+沙发区, CLASSROOM 几间教室, MECHANICAL 一排机柜),
--   seed 时按 floor_type 用固定模板生成 rooms 数组, 前端直接读坐标画 SVG。
--   结构:
--     {
--       "rooms": [{"name": "教室A", "x": 0, "y": 0, "w": 4, "h": 3, "usage": "CLASSROOM"}],
--       "schedule": "9-18 weekday"
--     }
--   坐标用网格单位 (1 单位 = 1.5m), 前端等距投影时乘 1.5 转米。
--
-- 为什么 source_dataset 默认 'synthetic_floor':
--   BDG2 楼层数据全是虚拟生成的, 标记来源便于后续真实导入时一键清理:
--     DELETE FROM core.floor WHERE source_dataset = 'synthetic_floor'
--   不会误删真实楼层数据。
-- ----------------------------------------------------------------------------

CREATE TABLE core.floor (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    site_id         uuid NOT NULL REFERENCES core.site(id) ON DELETE CASCADE,
    building_id     uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    floor_number    integer NOT NULL,
    floor_name      text NOT NULL,
    floor_type      text NOT NULL CHECK (floor_type IN
                    ('LOBBY','CLASSROOM','OFFICE','LAB','MECHANICAL','LIBRARY',
                     'SPORTS','STUDENT_CENTER','OTHER')),
    area_sqm        numeric(14,2) NOT NULL,
    is_rooftop      boolean NOT NULL DEFAULT false,
    source_dataset  text NOT NULL DEFAULT 'synthetic_floor',
    source_ref      text,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (building_id, floor_number)
);

CREATE INDEX idx_floor_building ON core.floor(building_id, floor_number);
CREATE INDEX idx_floor_tenant ON core.floor(tenant_id);


-- ----------------------------------------------------------------------------
-- B2. core.point 加 floor_id 外键
--
-- 存什么: 楼层级 SENSOR point 指向所属楼层。楼栋级 METER 总表 floor_id = NULL,
-- 楼层级 SENSOR floor_id 指向 core.floor.id。
--
-- 为什么 ON DELETE SET NULL 不 CASCADE:
--   楼层虚拟数据重建场景 (重跑 floor_synthetic seed) 会 DELETE core.floor, 如果
--   CASCADE 会把 point 一起删, 连带 fact.point_reading 失联。SET NULL 让 point
--   行保留 (point_code 还在), 重新建 floor 后再 UPDATE floor_id 接回去。
--
-- 为什么加部分索引 (WHERE floor_id IS NOT NULL):
--   楼栋级 METER (floor_id=NULL) 是 majority, 不参与楼层查询, 索引带上它们浪费
--   空间还拖慢 INSERT。部分索引只覆盖楼层 SENSOR, 查"某楼层的所有 point"走索引
--   扫描, 楼栋级 METER 不进索引。
-- ----------------------------------------------------------------------------

ALTER TABLE core.point
    ADD COLUMN IF NOT EXISTS floor_id uuid REFERENCES core.floor(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_point_floor
    ON core.point(floor_id) WHERE floor_id IS NOT NULL;


-- ----------------------------------------------------------------------------
-- B3. mart.floor_daily_energy 楼层×能源×日聚合
--
-- 存什么: 一个楼层一种能源一天的总能耗 + max/min/avg + hour_count + EUI。前端
-- 楼层对比柱状图 / EUI 排名 / 能源构成饼图 都走这张表, 不重算 fact。
--
-- 为什么 PK 是 (floor_id, energy_type, date) 不带 building_id:
--   floor_id 已唯一确定 building (core.floor.building_id 外键), 加 building_id
--   进 PK 冗余。查询时 JOIN core.floor 拿 building_id 即可。
--
-- 为什么 eui_kwh_per_m2 在这里算不用前端算:
--   EUI = total_kwh / area_sqm, area 是楼层属性 (core.floor.area_sqm), 聚合时
--   一并算好物化, 前端拿到直接展示, 不用每个图表都 JOIN floor + 除法。
--
-- 为什么 source_batch_id ON DELETE SET NULL:
--   跟 mart.building_daily_energy 一致 (init.sql 现有设计), batch 删除时保留
--   聚合数据, 只是丢了溯源指针。
-- ----------------------------------------------------------------------------

CREATE TABLE mart.floor_daily_energy (
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    building_id     uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    floor_id        uuid NOT NULL REFERENCES core.floor(id) ON DELETE CASCADE,
    energy_type     text NOT NULL REFERENCES core.energy_type(energy_type),
    date            date NOT NULL,
    total_kwh       numeric(14,3) NOT NULL,
    max_kwh         numeric(14,3),
    min_kwh         numeric(14,3),
    avg_kwh         numeric(14,3),
    hour_count      integer NOT NULL,
    eui_kwh_per_m2  numeric(10,3),
    source_batch_id uuid REFERENCES ingest.import_batch(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (floor_id, energy_type, date)
);

CREATE INDEX idx_floor_daily_building ON mart.floor_daily_energy(building_id, date DESC);
CREATE INDEX idx_floor_daily_floor_date ON mart.floor_daily_energy(floor_id, date DESC);


-- ----------------------------------------------------------------------------
-- B4. mart.point_status_snapshot 设备状态物化表
--
-- 存什么: 一个 point 一行, 当前状态 (ONLINE/OFFLINE/FAULT/STALE) + 最近读数 +
-- 完整率 + 故障原因。前端设备状态列表走这张表, 不实时扫 fact.point_reading。
--
-- 为什么不用 core.point.metadata jsonb 存状态:
--   静态属性 (型号/安装楼层/用途) 进 metadata 合理, 状态是动态的 (随时间变化),
--   塞 jsonb 每次更新都要重写整行, 而且查"某楼栋所有 OFFLINE 设备"走 jsonb 全
--   表扫描。物化进独立表 + 状态索引, 查询走索引扫描。
--
-- 状态语义:
--   ONLINE  完整率 >= 95% (近 7d)
--   STALE   完整率 50-95% (数据稀疏但还有)
--   OFFLINE 近 24h 完全无数据
--   FAULT   检测到持续异常 (连续 6h 超基线 3σ 或全零)
--
-- 为什么 PK 是 point_id 不是 (point_id, ts):
--   只要"当前状态", 不要历史。如果要历史状态 (比如看设备故障趋势), 后续另建
--   mart.point_status_history 表, 这里只存最新快照。
-- ----------------------------------------------------------------------------

CREATE TABLE mart.point_status_snapshot (
    point_id        uuid PRIMARY KEY REFERENCES core.point(id) ON DELETE CASCADE,
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    building_id     uuid NOT NULL REFERENCES core.building(id) ON DELETE CASCADE,
    floor_id        uuid REFERENCES core.floor(id) ON DELETE SET NULL,
    status          text NOT NULL CHECK (status IN ('ONLINE','OFFLINE','FAULT','STALE')),
    last_reading_ts timestamptz,
    last_reading_val double precision,
    completeness_pct numeric(5,2),
    fault_reason    text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_point_status_building ON mart.point_status_snapshot(building_id, status);
CREATE INDEX IF NOT EXISTS idx_point_status_floor
    ON mart.point_status_snapshot(floor_id, status) WHERE floor_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- Step 11a: 扩展 ingest.upload_session + ingest.import_batch 的 target_type
-- CHECK 约束, 加 FLOOR
-- ----------------------------------------------------------------------------
-- 原约束只允许 POINT/WEATHER/BUILDING (upload_session) 或 SPACE/POINT/ENERGY/
-- WEATHER (import_batch)。FLOOR 上传 (楼层实体信息) 走单独的 commit_floors 流程,
-- 不写 staging_reading, 直接落 core.floor, 但仍会建 import_batch 记录 (status
-- 直接 SUCCEEDED, 不走 merge)。
-- 用 ALTER TABLE 替换约束 (DROP 旧的 + ADD 新的), 因为 PG 不支持 ALTER
-- CONSTRAINT 改 CHECK 的内容, 只能 drop + add。
--
-- 已存在的 session / batch 不受影响 (CHECK 只在 INSERT/UPDATE 时校验)。
-- ----------------------------------------------------------------------------

ALTER TABLE ingest.upload_session DROP CONSTRAINT IF EXISTS upload_session_target_type_check;
ALTER TABLE ingest.upload_session
    ADD CONSTRAINT upload_session_target_type_check
    CHECK (target_type IN ('POINT', 'WEATHER', 'BUILDING', 'FLOOR'));

-- import_batch.target_type 原约束是 SPACE/POINT/ENERGY/WEATHER (跟 upload_session
-- 不同, 历史 reason)。加 FLOOR 让 commit_floors 能建 batch 记录。
ALTER TABLE ingest.import_batch DROP CONSTRAINT IF EXISTS import_batch_target_type_check;
ALTER TABLE ingest.import_batch
    ADD CONSTRAINT import_batch_target_type_check
    CHECK (target_type IN ('SPACE', 'POINT', 'ENERGY', 'WEATHER', 'FLOOR'));
