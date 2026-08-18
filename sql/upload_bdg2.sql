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
