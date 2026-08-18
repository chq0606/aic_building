"""
IVFFlat 索引重建。

IVFFlat 是一种基于聚类的近似最近邻索引,建索引时要先扫一遍数据确定聚类中心,
所以建议在数据导入后重建一次,让中心点分布更贴合实际数据。

knowledge.chunk 空表时跳过(没数据建索引没意义,lists=100 也聚不出 100 个中心)。
非空时 DROP + CREATE 重建。

lists 参数:pgvector 官方建议 lists = sqrt(行数)。这里一期固定 100,
够用(知识库预计几千到几万 chunk);后期数据量大了再改成动态算。
"""
from loguru import logger

from app.db.session import get_conn


def rebuild_ivfflat_if_needed() -> bool:
    """
    knowledge.chunk 非空时重建 IVFFlat 索引。
    返回是否实际重建了。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM knowledge.chunk")
            count = cur.fetchone()[0]

    if count == 0:
        logger.info("knowledge.chunk 为空,跳过 IVFFlat 索引重建")
        return False

    logger.info("knowledge.chunk 有 {} 行,重建 IVFFlat 索引...", count)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP INDEX IF EXISTS knowledge.idx_chunk_embedding")
            cur.execute("""
                CREATE INDEX idx_chunk_embedding
                ON knowledge.chunk USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """)
        conn.commit()
    logger.info("IVFFlat 索引重建完成")
    return True
