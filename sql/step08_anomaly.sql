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
