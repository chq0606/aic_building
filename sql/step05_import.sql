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
