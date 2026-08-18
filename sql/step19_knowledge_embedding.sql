-- ============================================================================
-- Step 19: 知识库 - document 表加 embedding 状态 + embedded_chunk_count
-- ----------------------------------------------------------------------------
-- 背景:
--   原 status CHECK 只允许 pending/parsing/parsed/failed。embed_document 跑
--   时不改 status, 前端 GET /documents 看不到"向量化中/已完成", 只能盲目等。
--
-- 改动:
--   1. status CHECK 加 'embedding' - embed 开始时设此值
--   2. 加 embedded_chunk_count integer - 记录已向量化 chunk 数, 0 表示没跑过
--   3. embed 完成 status 回到 'parsed' (文档仍可被检索), embedded_chunk_count 写总数
--
-- 状态机:
--   pending  -> parsing -> parsed (解析完成)
--                       -> failed (解析失败)
--   parsed   -> embedding -> parsed (向量化完成, embedded_chunk_count > 0)
--                          -> failed (向量化失败, parse_error 记原因, status 回 parsed
--                                     因为已解析仍可走 BM25 检索兜底)
--
-- 旧数据兼容: embedded_chunk_count 默认 0, 既有已 parsed 文档视为"未向量化",
-- 用户在 UI 上看到"已解析未向量化", 可手动点向量化按钮。
-- ============================================================================

ALTER TABLE knowledge.document
    DROP CONSTRAINT IF EXISTS document_status_check;

ALTER TABLE knowledge.document
    ADD CONSTRAINT document_status_check
    CHECK (status IN ('pending','parsing','parsed','embedding','failed'));

ALTER TABLE knowledge.document
    ADD COLUMN IF NOT EXISTS embedded_chunk_count integer NOT NULL DEFAULT 0;
