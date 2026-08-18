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
