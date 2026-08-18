"""知识库导入验收脚本。

跑法:
    cd backend
    "/e/anaconda/envs/building_aic/python.exe" verify_step09.py

流程:
    1. 查 demo tenant id
    2. 上传 GB 55015-2021 PDF
    3. 触发 parse (PyMuPDF + EasyOCR)
    4. 触发 embed (BGE-large-zh-v1.5)
    5. 查库验收: chunk 数 / embedding 维度 / search_vector / IVFFlat 索引

直接调 service 层函数, 不走 HTTP。这样不需要起 uvicorn,
也不需要 JWT, 跑得快。HTTP 层 (api/knowledge.py) 只是薄薄一层
wrapper, service 层跑通 HTTP 层大概率没问题。
"""
from pathlib import Path

from loguru import logger

from app.db.session import close_pool, get_conn, init_pool
from app.services.knowledge_service import (
    embed_document,
    list_documents,
    parse_document,
    upload_document,
)


# 验收用 PDF: GB 55015-2021 建筑节能与可再生能源利用通用规范
# 选这本是因为它是强制规范 (国强规范), 跟项目主题最贴, 条款结构清晰。
# 文件名里 "国强规范" 后面是 EM DASH (U+2014) 不是普通连字符, 写死容易
# 踩坑, 用 glob 找最稳。
_PDF_DIR = Path(__file__).parent.parent.parent / "test_pdfs"
_candidates = list(_PDF_DIR.glob("*55015-2021*.pdf"))
if not _candidates:
    raise FileNotFoundError(f"在 {_PDF_DIR} 找不到 GB 55015-2021 PDF")
PDF_PATH = _candidates[0]


def get_demo_tenant_id() -> str:
    """查 demo 租户 id (seed 时建的 demo 租户)。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code='demo'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 租户不存在, 先跑 seed_bdg2 建租户")
            return str(row[0])


def main() -> None:
    init_pool()
    try:
        tenant_id = get_demo_tenant_id()
        logger.info("demo tenant_id={}", tenant_id)

        # 1. 上传
        pdf_bytes = PDF_PATH.read_bytes()
        logger.info("上传 PDF: {} ({} bytes)", PDF_PATH.name, len(pdf_bytes))
        up = upload_document(
            tenant_id=tenant_id,
            filename=PDF_PATH.name,
            file_data=pdf_bytes,
            mime_type="application/pdf",
        )
        doc_id = up["doc_id"]
        logger.info("上传完成 doc_id={}", doc_id)

        # 2. parse (慢, OCR 一页 1-2s)
        logger.info("触发 parse...")
        parse_result = parse_document(doc_id, tenant_id)
        logger.info("parse 完成: {}", parse_result)

        # 3. embed
        logger.info("触发 embed...")
        embed_result = embed_document(doc_id, tenant_id)
        logger.info("embed 完成: {}", embed_result)

        # 4. 验收: 查库
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT count(*),
                           count(embedding),
                           avg(length(chunk_text))::int,
                           count(search_vector),
                           count(chunk_text_tokenized)
                    FROM knowledge.chunk
                    WHERE doc_id = %s
                """, (doc_id,))
                total, with_emb, avg_len, with_tsv, with_tok = cur.fetchone()
                logger.info("chunk 总数={} 有 embedding={} 平均长度={} 有 tsvector={} 有 tokenized={}",
                            total, with_emb, avg_len, with_tsv, with_tok)

                cur.execute("""
                    SELECT chunk_index, section_title, page_no,
                           left(chunk_text, 60) AS text_preview,
                           embedding IS NOT NULL AS has_emb,
                           vector_dims(embedding) AS emb_dim,
                           left(search_vector::text, 80) AS tsv_preview
                    FROM knowledge.chunk
                    WHERE doc_id = %s
                    ORDER BY chunk_index
                    LIMIT 3
                """, (doc_id,))
                for r in cur.fetchall():
                    logger.info("chunk[{}] section={} page={} dim={} tsv={} text={}...",
                                r[0], r[1], r[2], r[5], r[6], r[3])

                cur.execute("""
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname='knowledge' AND tablename='chunk'
                """)
                for r in cur.fetchall():
                    logger.info("索引 {}: {}", r[0], r[1])

        docs = list_documents(tenant_id)
        logger.info("租户文档数={} 当前文档 status={}",
                    len(docs), docs[0]["status"] if docs else "n/a")

    finally:
        close_pool()


if __name__ == "__main__":
    main()
