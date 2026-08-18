"""
知识库文档导入编排服务。

负责把上传的 PDF 解析、切分、写库。状态流:
  pending  -> parsing -> parsed (status by parse step)
                       -> failed (parse_error 记录原因)

embed 步骤不改 status, 只填 chunk.embedding。失败时 chunk.embedding
保持 NULL, 检索时走 BM25 兜底 (search_vector)。

异常隔离: 单文档解析失败不影响其他文档。失败原因写 parse_error 字段,
前端可读。

文件存储: 本地目录, 路径 = knowledge_upload_dir/{tenant_id}/{doc_id}.pdf。
生产环境换对象存储时, 把 _save_file / _delete_file 改了就行, 业务逻辑不变。

standard_no 提取: 从文件名正则匹配 "GB/T xxxxx-yyyy" / "GB xxxxx-yyyy"。
存到 metadata jsonb 里, 检索时可按标准号过滤。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jieba
from loguru import logger

from app.core.config import settings
from app.db.session import get_conn
from .chunker import chunk_pages
from .embedding_service import embed_texts, format_vector_for_pg
from .pdf_parser import parse_pdf


# 上传文件大小限制: 200MB。国标 PDF 实际 5-110MB (GB 55015-2021 ~109MB),
# 50MB 太严, 提到 200MB 给足余量。再大就要走对象存储 + 分片上传, 一期不做。
MAX_FILE_SIZE = 200 * 1024 * 1024

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".pdf"}

# 标准号正则: GB/T 12345-2020 / GB 12345-2020 / GB/T 12345.1-2020
# 文件名里 GB 后面可能没空格 (GB50365-2019), 也可能多个空格 (GB T 19232)
STANDARD_NO_PATTERN = re.compile(r"GB[/\s]*T?\s*\d+(?:\.\d+)?-\d+")

# 书名号里的内容当标题
TITLE_PATTERN = re.compile(r"《(.+?)》")


class KnowledgeError(Exception):
    """知识库业务错误, 给 API 层用。"""


def _extract_standard_no(filename: str) -> str | None:
    """从文件名提取标准号。返回规整化的 "GB/T 12345-2020" 形式。"""
    m = STANDARD_NO_PATTERN.search(filename)
    if not m:
        return None
    raw = m.group(0)
    # 规整化: 去多余空格, "GB/T" 中间不能有空格
    raw = re.sub(r"\s+", "", raw)
    # 把 "GBT" 改成 "GB/T" (无空格的情况)
    raw = re.sub(r"^GBT", "GB/T", raw)
    return raw


def _extract_title(filename: str) -> str:
    """从文件名提取标题 (书名号内)。没书名号就用文件名 (去扩展名)。"""
    m = TITLE_PATTERN.search(filename)
    if m:
        return m.group(1).strip()
    return Path(filename).stem


def _detect_doc_type(filename: str) -> str:
    """文件名前缀判 doc_type。返回值对齐数据库 document 表的 CHECK 约束
    ('standard','manual','sop','report','paper','other')。

    所有标准 (国标/地标/行标) 统一归 'standard'。强制规范 vs 推荐规范
    的区别靠 metadata 字段存, doc_type 不细分 (DB 字段不区分这两类)。
    """
    if any(filename.startswith(p) for p in ("国家标准", "国强规范", "地方标准", "行业标准")):
        return "standard"
    return "other"


def _tokenize_for_search(text: str) -> str:
    """jieba 分词后用空格连接, 给 tsvector('simple', ...) 用。

    'simple' 配置按空格切分 + 小写化, 不做语干。jieba 出来是空格分隔的词,
    跟 simple 配合刚好对上。这也是 step09_knowledge.sql 加 chunk_text_tokenized
    字段的原因。
    """
    words = jieba.cut_for_search(text)
    return " ".join(w.strip() for w in words if w.strip())


def _save_file(tenant_id: str, doc_id: str, file_data: bytes, ext: str) -> str:
    """把上传文件存到本地。返回相对 storage 的路径 (写库 source_path 用)。"""
    storage_root = settings.knowledge_upload_path
    tenant_dir = storage_root / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"{tenant_id}/{doc_id}{ext}"
    abs_path = storage_root / rel_path
    abs_path.write_bytes(file_data)
    return rel_path


def _delete_file(rel_path: str) -> None:
    """删本地文件。文件不存在不报错。"""
    if not rel_path:
        return
    abs_path = settings.knowledge_upload_path / rel_path
    try:
        if abs_path.exists():
            abs_path.unlink()
    except OSError as e:
        logger.warning("删除本地文件失败 {}: {}", abs_path, e)


def upload_document(
    tenant_id: str,
    filename: str,
    file_data: bytes,
    mime_type: str,
) -> dict:
    """上传单个 PDF, 写 document 表 (status=pending)。

    同一文件 (SHA-256 相同) 重复上传, 抛 KnowledgeError 提示已存在。

    Returns:
        {"doc_id": str, "status": "pending"}
    """
    if len(file_data) > MAX_FILE_SIZE:
        raise KnowledgeError(f"文件过大: {len(file_data)} bytes, 上限 {MAX_FILE_SIZE}")

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise KnowledgeError(f"不支持的文件类型: {ext}, 只支持 PDF")

    file_hash = hashlib.sha256(file_data).hexdigest()
    standard_no = _extract_standard_no(filename)
    title = _extract_title(filename)
    doc_type = _detect_doc_type(filename)

    # 检查是否已存在同 hash 文件
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM knowledge.document
                WHERE tenant_id = %s AND file_hash = %s
            """, (tenant_id, file_hash))
            if cur.fetchone() is not None:
                raise KnowledgeError(f"该文件已上传 (SHA-256 重复), filename={filename}")

            # 生成 doc_id (uuid4, 写库时 PG 自动 cast 成 uuid 类型)
            doc_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            metadata = {"standard_no": standard_no} if standard_no else {}

            # source_path 需要先用 doc_id 拼出来再存文件, 再 update 写回
            # 一期简化: 先 INSERT 占位 source_path="", 文件存好后 UPDATE
            rel_path = f"{tenant_id}/{doc_id}.pdf"
            _save_file(tenant_id, doc_id, file_data, ".pdf")
            cur.execute("""
                INSERT INTO knowledge.document
                    (id, tenant_id, doc_type, title, source_path, file_hash,
                     mime_type, status, metadata, uploaded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
            """, (
                doc_id, tenant_id, doc_type, title, rel_path, file_hash,
                mime_type, json.dumps(metadata), now,
            ))
            conn.commit()

    logger.info("上传文档 doc_id={} title={} standard_no={}", doc_id, title, standard_no)
    # 同时返 id 和 doc_id, 前端 KnowledgeDocument 类型用 id (跟 list_documents 返回对齐),
    # 老 code 用 doc_id 的地方 (如 parse_document 内部) 不受影响
    return {"id": doc_id, "doc_id": doc_id, "status": "pending"}


def parse_document(doc_id: str, tenant_id: str) -> dict:
    """后台解析文档: PDF -> 文本 -> 切分 -> 写 chunk。

    状态流: pending -> parsing -> parsed (成功) / failed (失败)
    chunk.embedding 此时还是 NULL, 由 embed_document 单独触发。

    这个函数设计为同步执行 (FastAPI BackgroundTasks), 不返回进度。
    失败原因写 document.parse_error, 前端 GET /documents 能看到。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 校验文档存在且属于租户
            cur.execute("""
                SELECT id, source_path, status FROM knowledge.document
                WHERE id = %s AND tenant_id = %s
            """, (doc_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise KnowledgeError(f"文档不存在或无权访问: {doc_id}")
            doc_id, source_path, old_status = row

            # 切到 parsing
            cur.execute("""
                UPDATE knowledge.document
                SET status = 'parsing', parse_error = NULL
                WHERE id = %s
            """, (doc_id,))
            conn.commit()

        # 解析 + 切分 (放 try 外, 失败时记 parse_error)
        try:
            abs_path = settings.knowledge_upload_path / source_path
            if not abs_path.exists():
                raise FileNotFoundError(f"PDF 文件丢失: {abs_path}")

            pages = parse_pdf(abs_path)
            chunks = chunk_pages(pages)

            # jieba 预分词, 给 search_vector 用
            chunk_texts = [c.chunk_text for c in chunks]
            tokenized_texts = [_tokenize_for_search(t) for t in chunk_texts]

            # 落 chunk 表
            with conn.cursor() as cur:
                # 先删旧 chunk (重试场景, 上次半成功的 chunk 要清掉)
                cur.execute("DELETE FROM knowledge.chunk WHERE doc_id = %s", (doc_id,))

                page_count = len(pages)
                chunk_count = len(chunks)
                now = datetime.now(timezone.utc)

                # 批量插入用 execute_values 提速
                from psycopg2.extras import execute_values
                values = [
                    (
                        doc_id, idx, c.chunk_text, tokenized_texts[idx],
                        c.page_no, c.section_title, c.section_path,
                        json.dumps({"source": "pdf_ocr" if pages[c.page_no-1].source == "ocr" else "pdf_text"}),
                        now,
                    )
                    for idx, c in enumerate(chunks)
                ]
                execute_values(cur, """
                    INSERT INTO knowledge.chunk
                        (doc_id, chunk_index, chunk_text, chunk_text_tokenized,
                         page_no, section_title, section_path, metadata, created_at)
                    VALUES %s
                """, values)

                # trigger trg_chunk_tsv 会自动算 search_vector
                cur.execute("""
                    UPDATE knowledge.document
                    SET status = 'parsed', page_count = %s, chunk_count = %s,
                        parsed_at = %s, parse_error = NULL
                    WHERE id = %s
                """, (page_count, chunk_count, now, doc_id))
                conn.commit()

            logger.info("解析完成 doc_id={} pages={} chunks={}",
                        doc_id, page_count, chunk_count)
            return {"doc_id": doc_id, "status": "parsed",
                    "page_count": page_count, "chunk_count": chunk_count}

        except Exception as e:
            logger.exception("解析失败 doc_id={}", doc_id)
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE knowledge.document
                    SET status = 'failed', parse_error = %s
                    WHERE id = %s
                """, (str(e)[:500], doc_id))
                conn.commit()
            raise KnowledgeError(f"解析失败: {e}")


def embed_document(doc_id: str, tenant_id: str) -> dict:
    """给已解析文档的 chunk 算 embedding 并写库。

    要求 status='parsed', 否则报错。chunk.embedding 已有的会重算覆盖
    (重跑 embed 用于换模型)。

    状态切换: parsed -> embedding -> parsed (embedded_chunk_count > 0)
    失败时 status 回 parsed (已解析仍可走 BM25 检索兜底), parse_error 记原因。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status FROM knowledge.document
                WHERE id = %s AND tenant_id = %s
            """, (doc_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise KnowledgeError(f"文档不存在或无权访问: {doc_id}")
            if row[0] != "parsed":
                raise KnowledgeError(f"文档状态 {row[0]}, 需先解析到 parsed 才能跑 embed")

            # 切到 embedding, 让前端能看到"向量化中"
            cur.execute("""
                UPDATE knowledge.document
                SET status = 'embedding', parse_error = NULL
                WHERE id = %s
            """, (doc_id,))
            conn.commit()

            cur.execute("""
                SELECT id, chunk_text FROM knowledge.chunk
                WHERE doc_id = %s AND chunk_text IS NOT NULL
                ORDER BY chunk_index
            """, (doc_id,))
            chunk_rows = cur.fetchall()

        if not chunk_rows:
            logger.warning("文档无 chunk 可 embed doc_id={}", doc_id)
            # 无 chunk 也算"向量化完成", embedded_chunk_count=0
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE knowledge.document
                    SET status = 'parsed', embedded_chunk_count = 0
                    WHERE id = %s
                """, (doc_id,))
                conn.commit()
            return {"doc_id": doc_id, "embedded_count": 0}

        chunk_ids = [r[0] for r in chunk_rows]
        chunk_texts = [r[1] for r in chunk_rows]

        # 分批跑, 一批 32 条 (BGE batch=8 × 4 批, GPU 充分用起来)
        BATCH = 32
        embedded_count = 0
        try:
            for i in range(0, len(chunk_texts), BATCH):
                batch_texts = chunk_texts[i:i+BATCH]
                batch_ids = chunk_ids[i:i+BATCH]
                vectors = embed_texts(batch_texts)

                with conn.cursor() as cur:
                    for cid, vec in zip(batch_ids, vectors):
                        vec_str = format_vector_for_pg(vec)
                        cur.execute("""
                            UPDATE knowledge.chunk
                            SET embedding = %s::vector
                            WHERE id = %s
                        """, (vec_str, cid))
                    # 中途也更新 embedded_chunk_count, 让前端能看到进度
                    # (虽然前端轮询 GET /documents 时, status=embedding 期间
                    # 也能看到 embedded_chunk_count 在涨)
                    embedded_count += len(batch_texts)
                    cur.execute("""
                        UPDATE knowledge.document
                        SET embedded_chunk_count = %s
                        WHERE id = %s
                    """, (embedded_count, doc_id))
                    conn.commit()
                logger.info("embed 进度 doc_id={} {}/{}", doc_id, embedded_count, len(chunk_texts))

            # 全部完成, status 回 parsed (终态)
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE knowledge.document
                    SET status = 'parsed', embedded_chunk_count = %s
                    WHERE id = %s
                """, (embedded_count, doc_id))
                conn.commit()

            return {"doc_id": doc_id, "embedded_count": embedded_count}

        except Exception as e:
            logger.exception("embedding 失败 doc_id={}", doc_id)
            # 失败回 parsed (已解析仍可走 BM25 兜底), parse_error 记原因
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE knowledge.document
                    SET status = 'parsed', parse_error = %s
                    WHERE id = %s
                """, (f"embedding 失败: {e}"[:500], doc_id))
                conn.commit()
            raise KnowledgeError(f"embedding 失败: {e}")


def list_documents(tenant_id: str, status: str | None = None) -> list[dict]:
    """列出租户所有文档。可按 status 过滤。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute("""
                    SELECT id, doc_type, title, source_path, file_hash, mime_type,
                           page_count, chunk_count, status, parse_error, metadata,
                           uploaded_at, parsed_at, embedded_chunk_count
                    FROM knowledge.document
                    WHERE tenant_id = %s AND status = %s
                    ORDER BY uploaded_at DESC
                """, (tenant_id, status))
            else:
                cur.execute("""
                    SELECT id, doc_type, title, source_path, file_hash, mime_type,
                           page_count, chunk_count, status, parse_error, metadata,
                           uploaded_at, parsed_at, embedded_chunk_count
                    FROM knowledge.document
                    WHERE tenant_id = %s
                    ORDER BY uploaded_at DESC
                """, (tenant_id,))
            rows = cur.fetchall()

    results = []
    for r in rows:
        # 同时返 id 和 doc_id: 前端 KnowledgeDocument 类型用 id (跟 upload_document
        # 返回对齐), 老 code 用 doc_id 的地方不受影响。这两个指向同一个 UUID。
        results.append({
            "id": r[0],
            "doc_id": r[0],
            "doc_type": r[1],
            "title": r[2],
            "source_path": r[3],
            "file_hash": r[4],
            "mime_type": r[5],
            "page_count": r[6],
            "chunk_count": r[7],
            "status": r[8],
            "parse_error": r[9],
            "metadata": r[10] if isinstance(r[10], dict) else json.loads(r[10]) if r[10] else {},
            "uploaded_at": r[11].isoformat() if r[11] else None,
            "parsed_at": r[12].isoformat() if r[12] else None,
            "embedded_chunk_count": r[13] or 0,
        })
    return results


def delete_document(doc_id: str, tenant_id: str) -> dict:
    """删除文档: DB 记录 + chunk + 本地文件。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source_path FROM knowledge.document
                WHERE id = %s AND tenant_id = %s
            """, (doc_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise KnowledgeError(f"文档不存在或无权访问: {doc_id}")
            source_path = row[0]

            # ON DELETE CASCADE 会自动删 chunk, 这里显式删一次保险
            cur.execute("DELETE FROM knowledge.chunk WHERE doc_id = %s", (doc_id,))
            cur.execute("DELETE FROM knowledge.document WHERE id = %s", (doc_id,))
            conn.commit()

    _delete_file(source_path)
    logger.info("删除文档 doc_id={}", doc_id)
    return {"doc_id": doc_id, "deleted": True}
