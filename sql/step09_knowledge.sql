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
