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
