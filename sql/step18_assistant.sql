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
