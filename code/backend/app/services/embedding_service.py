"""
BGE-large-zh-v1.5 embedding 推理服务。

把文本转 1024 维向量, 给知识库 chunk 表用。向量库是 pgvector,
写入时直接传 "[0.1,0.2,...]" 字符串, 不依赖 pgvector python 包。

模型路径配置:
  - settings.bge_model_path 不空: 用本地路径
  - 空: 从 HuggingFace 下 BAAI/bge-large-zh-v1.5 (首次 ~1.3GB)
  - 国内下不动可设 HF_ENDPOINT=https://hf-mirror.com 环境变量走镜像

文档侧 embedding 不加 query 前缀 ("为这个句子生成表示以用于检索相关文章: ")。
BGE 文档说前缀只用于检索 query 侧, 文档侧加了反而掉点。检索 query 侧
前缀在 retrieval_service (Step 10) 加, 这里不管。

batch 推理: 一次塞多条文本进 GPU, 比单条循环快 5-10x。batch=8 是
RTX 5060 8GB 显存的安全值, BGE-large 325M 参数, 8 × 1024 token
占用约 4GB, 留余量给其他进程。

normalize_embeddings=True: BGE 推荐 L2 归一化, 后续相似度直接用
dot product (= cosine), 跟 ivfflat vector_cosine_ops 索引对得上。
"""
from __future__ import annotations

import os

# BGE 模型走 HuggingFace cache, 国内连不上 huggingface.co 会卡在 HEAD 请求重试。
# 设 HF_HUB_OFFLINE=1 跳过 metadata 校验, 直接用本地缓存。模型首次下载走
# hf-mirror (见 requirements.txt 注释), 下完后离线模式最稳。
# setdefault 不覆盖用户已设的值, 方便 dev 调试时手动切回在线模式。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import torch
from loguru import logger
from sentence_transformers import SentenceTransformer

from app.core.config import settings


# 模块级单例。首次 embed_texts 调用时加载, 加载 ~5s。
# 不调 embedding 的服务 (e.g. 只跑异常检测) 不会触发加载。
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        path = settings.bge_model_path or "BAAI/bge-large-zh-v1.5"
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("加载 BGE 模型: {} device={}", path, device)
        _model = SentenceTransformer(path, device=device)
        logger.info("BGE 模型加载完成, dim={}", _model.get_sentence_embedding_dimension())
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """把多条文本转 1024 维向量, L2 归一化。

    Args:
        texts: 文本列表, 单条建议 <= 800 字 (chunker.MAX_CHUNK_CHARS)

    Returns:
        list[list[float]], 长度 = len(texts), 每条 1024 维

    Raises:
        RuntimeError: GPU 推理失败 (OOM 等), 上层降级 batch=1 重试或报错
    """
    if not texts:
        return []

    model = _get_model()
    embeddings = model.encode(
        texts,
        batch_size=8,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return embeddings.tolist()


def embed_one(text: str) -> list[float]:
    """单条文本 embedding, 给检索 query 用 (Step 10)。"""
    return embed_texts([text])[0]


def format_vector_for_pg(vec: list[float]) -> str:
    """把向量格式化成 PG vector 类型能解析的字符串 "[v1,v2,...]"。

    psycopg2 不带 pgvector 适配器, 直接传字符串 PG 会自己 cast。
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
