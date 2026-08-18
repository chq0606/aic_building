-- ============================================================================
-- Step 16 补充: 建筑基础信息上传 (target_type=BUILDING)
-- ----------------------------------------------------------------------------
-- 之前 buildings.csv 模板存在 (upload_session.target_type 已含 BUILDING), 但
-- commit 流程没接: import_batch.target_type 的 CHECK 约束漏了 BUILDING, 导致
-- commit_buildings 建 batch 记录时违反约束 (CheckViolation) 返 500。
--
-- 这里给 import_batch 加 BUILDING (跟 upload_session 对齐)。BUILDING 跟 FLOOR
-- 一样不走 staging/merge, 直接落 core.building, batch 状态直接 SUCCEEDED。
-- ============================================================================

ALTER TABLE ingest.import_batch DROP CONSTRAINT IF EXISTS import_batch_target_type_check;
ALTER TABLE ingest.import_batch
    ADD CONSTRAINT import_batch_target_type_check
    CHECK (target_type IN ('SPACE', 'POINT', 'ENERGY', 'WEATHER', 'FLOOR', 'BUILDING'));
