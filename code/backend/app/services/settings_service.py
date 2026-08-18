"""
Step 17 系统设置 - 用户级配置服务。

职责:
  - GLM API Key 加密存储 (按 user_id 隔离)
  - BGE 模型信息只读查询 (BGE 是后端启动时 singleton 加载, 不能按用户切换)
  - GLM 连接测试 (调智谱 API 发一条 ping)
  - BGE 连接测试 (embed 一条测试文本看维度)

加密方案:
  AES-256-GCM。密钥从 settings.jwt_secret 派生:
    PBKDF2-HMAC-SHA256(password=jwt_secret, salt=固定常量, iterations=100k, dklen=32)
  派生函数模块级 cache (一次计算), 整个进程共用。
  固定 salt 用项目命名空间字符串, 不入库不轮换 (跟 jwt_secret 绑定,
  改了 jwt_secret 老密文就解不开了, 跟改 jwt_secret 必须重新登录是同样道理)。

  密文格式: base64(nonce(12) || ciphertext || tag(16))
  nonce 每次加密随机生成, 同样的明文加密两次结果不同, 防字典攻击。

GLM 连接测试为什么不引 zhipuai SDK:
  zhipuai SDK 是个轻量 http wrapper, 我们只需要发一条 ping 消息看是否 200,
  没必要多引一个依赖。直接 requests.post 到智谱 v4 endpoint,
  返 200 + 含 choices 字段就算连通, 返 401 就是 Key 无效, 返其他按状态码报错。

为什么不复用 retrieval/embedding 的 GLM 调用:
  Step 18 才会建 llm_service.py 封装 GLM 调用 (含 chat/completions + tools)。
  Step 17 在它之前, 不依赖未实现的模块。Step 18 实现后这个测试函数可以保留
  作为"健康探测"专用 (max_tokens=5 不走完整对话, 省钱省时间)。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from loguru import logger

from app.core.config import settings


# AES-GCM 推荐参数。nonce 12 字节是 NIST 推荐, 16 字节反而不安全 (GCM 模式规范)。
_NONCE_LEN = 12
_KEY_LEN = 32  # AES-256

# PBKDF2 派生参数。100k 次迭代是 2023 年 OWASP 推荐下限, 计算一次 ~80ms 可接受。
# salt 用项目命名空间固定字符串: 不入库不轮换, 跟 jwt_secret 绑死。
# 改 jwt_secret 会让所有加密的 GLM Key 解不开 (跟改 jwt_secret 必须重登同理,
# 是可接受的运维代价)。
_PBKDF2_SALT = b"aic-building/user-settings/v1"
_PBKDF2_ITERATIONS = 100_000


# 模块级 cache 派生密钥, 进程内只算一次。
_derived_key_cache: bytes | None = None


def _derive_key() -> bytes:
    """从 jwt_secret 派生 32 字节 AES-256 密钥。

    派生而非直接 hash: 直接 SHA-256 出来的 32 字节虽然能用, 但缺少
    "密钥拉伸" 防暴力破解。jwt_secret 在 dev 默认值很简单, PBKDF2 加 100k
    迭代能让爆破成本高几个数量级。
    """
    global _derived_key_cache
    if _derived_key_cache is None:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_KEY_LEN,
            salt=_PBKDF2_SALT,
            iterations=_PBKDF2_ITERATIONS,
        )
        _derived_key_cache = kdf.derive(settings.jwt_secret.encode("utf-8"))
    return _derived_key_cache


def _encrypt(plaintext: str) -> str:
    """AES-GCM 加密, 返 base64(nonce || ciphertext || tag)。

    AESGCM.encrypt 返回的 bytes 已经包含 tag (末尾 16 字节), 不需要单独存。
    这里把 nonce 拼到前面, 解密时拆出来, 一条字段存完整密文。
    """
    key = _derive_key()
    aes = AESGCM(key)
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def _decrypt(blob: str) -> str:
    """AES-GCM 解密。密文格式错或 tag 校验失败会抛 InvalidTag, 上层吞掉返 None。"""
    key = _derive_key()
    aes = AESGCM(key)
    raw = base64.b64decode(blob)
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return aes.decrypt(nonce, ct, associated_data=None).decode("utf-8")


def _make_hint(plaintext: str) -> str:
    """生成脱敏提示, 如 'sk-***ab12'。

    智谱 API Key 格式: 形如 'xxxxxxxx.xxxxxx' (8位点分6位) 或一长串字符。
    保留末 4 位让用户能识别 "是不是上次配的那个 Key", 前面星号脱敏。
    不到 4 位的全星号。
    """
    s = plaintext.strip()
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]


# ---------------------------------------------------------------------------
# GLM API Key 存取
# ---------------------------------------------------------------------------


@dataclass
class GlmKeyStatus:
    """前端展示用的脱敏状态, 不暴露明文。"""
    configured: bool
    hint: str | None
    updated_at: datetime | None


def get_glm_key_status(conn, user_id: str) -> GlmKeyStatus:
    """查当前用户的 GLM Key 配置状态 (脱敏, 不返明文)。

    拿明文走 get_glm_api_key, 这个函数只给前端展示用。
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT glm_api_key_encrypted, glm_api_key_hint, updated_at
            FROM core.user_settings
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    if row is None or not row[0]:
        return GlmKeyStatus(configured=False, hint=None, updated_at=None)
    return GlmKeyStatus(
        configured=True,
        hint=row[1],
        updated_at=row[2],
    )


def get_glm_api_key(conn, user_id: str) -> str | None:
    """取当前用户的 GLM API Key 明文。

    service 层内部用, 不通过 API 暴露给前端。Step 18 的 llm_service
    会调这个函数拿 Key 调智谱 API。

    解密失败 (密钥变了/密文损坏) 返 None 并打 warning, 不抛异常让上层崩。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT glm_api_key_encrypted FROM core.user_settings WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None or not row[0]:
        return None
    try:
        return _decrypt(row[0])
    except Exception as e:
        # 密钥换了或密文损坏, 解不开就当未配置, 让用户重新配。
        logger.warning("GLM API Key 解密失败 user_id={}: {}", user_id, e)
        return None


def set_glm_api_key(conn, user_id: str, plaintext: str) -> GlmKeyStatus:
    """保存 GLM API Key (加密入库 + 写脱敏提示)。

    upsert 语义: 已存在记录就 UPDATE, 不存在就 INSERT。
    """
    plain = plaintext.strip()
    if not plain:
        raise ValueError("API Key 不能为空")

    encrypted = _encrypt(plain)
    hint = _make_hint(plain)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.user_settings (user_id, glm_api_key_encrypted, glm_api_key_hint, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (user_id) DO UPDATE SET
                glm_api_key_encrypted = EXCLUDED.glm_api_key_encrypted,
                glm_api_key_hint = EXCLUDED.glm_api_key_hint,
                updated_at = now()
            """,
            (user_id, encrypted, hint),
        )
        cur.execute(
            """
            SELECT glm_api_key_hint, updated_at
            FROM core.user_settings
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    conn.commit()
    return GlmKeyStatus(
        configured=True,
        hint=row[0],
        updated_at=row[1],
    )


def clear_glm_api_key(conn, user_id: str) -> None:
    """清空 GLM API Key 配置。前端没暴露这个动作, 留给后续可能用。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE core.user_settings
            SET glm_api_key_encrypted = NULL,
                glm_api_key_hint = NULL,
                updated_at = now()
            WHERE user_id = %s
            """,
            (user_id,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# GLM 连接测试
# ---------------------------------------------------------------------------


@dataclass
class GlmTestResult:
    ok: bool
    message: str
    detail: str | None = None  # 出错时的诊断信息, 比如 HTTP 响应体片段


def test_glm_connection(api_key: str, timeout: float = 15.0) -> GlmTestResult:
    """发一条 max_tokens=5 的 ping 到智谱 API, 看能否正常返回。

    不引 zhipuai SDK, 直接 requests.post。返回 200 + 含 choices 字段就算通过,
    否则按 HTTP 状态码给出诊断 (401 = Key 无效 / 429 = 限流 / 其他)。

    timeout 15s 包含建连 + 推理, 慢一点 (LLM 首字延迟) 不算失败。
    """
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.glm_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.exceptions.Timeout:
        return GlmTestResult(ok=False, message="请求超时, 检查网络或智谱服务状态")
    except requests.exceptions.ConnectionError as e:
        return GlmTestResult(ok=False, message="无法连接智谱 API", detail=str(e)[:200])
    except Exception as e:
        return GlmTestResult(ok=False, message=f"请求异常: {e}", detail=str(e)[:200])

    if resp.status_code == 200:
        try:
            data = resp.json()
            # 返 200 但响应体结构不对也算不通过
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0].get("message", {}).get("content", "")
                return GlmTestResult(
                    ok=True,
                    message=f"连通正常, 模型返: {content[:30]}",
                    detail=f"model={data.get('model', '?')}",
                )
            return GlmTestResult(
                ok=False,
                message="响应结构异常, 缺少 choices 字段",
                detail=str(data)[:200],
            )
        except Exception as e:
            return GlmTestResult(ok=False, message="响应 JSON 解析失败", detail=str(e)[:200])

    # 非 200, 按状态码给诊断
    detail = resp.text[:200] if resp.text else ""
    if resp.status_code == 401:
        return GlmTestResult(ok=False, message="API Key 无效或已过期", detail=detail)
    if resp.status_code == 403:
        return GlmTestResult(ok=False, message="无权限调用此模型, 检查 Key 权限", detail=detail)
    if resp.status_code == 429:
        return GlmTestResult(ok=False, message="请求被限流, 稍后重试", detail=detail)
    return GlmTestResult(
        ok=False,
        message=f"智谱 API 返 {resp.status_code}",
        detail=detail,
    )


# ---------------------------------------------------------------------------
# BGE 模型信息查询 + 连接测试
# ---------------------------------------------------------------------------


@dataclass
class BgeStatus:
    """BGE 模型当前状态, 前端展示用。"""
    configured: bool        # settings.bge_model_path 是否非空
    model_path: str         # 当前配置的路径 (空就显示"未配置, 用默认 BAAI/bge-large-zh-v1.5")
    expected_dim: int       # 期望维度 (settings.bge_dim)
    actual_dim: int | None  # 实际加载后维度, None = 未加载过
    loaded: bool            # 是否已加载到内存
    device: str | None      # 加载到哪个 device (cuda / cpu), None = 未加载


def get_bge_status() -> BgeStatus:
    """查 BGE 当前状态, 不触发加载。

    只读 embedding_service._model 单例, 已加载就返维度, 没加载就返 None。
    不主动调 _get_model() 避免触发 5s 加载。
    """
    from app.services import embedding_service  # 延迟 import 避免循环依赖
    model = embedding_service._model
    path = settings.bge_model_path or "BAAI/bge-large-zh-v1.5"
    if model is None:
        return BgeStatus(
            configured=bool(settings.bge_model_path),
            model_path=path,
            expected_dim=settings.bge_dim,
            actual_dim=None,
            loaded=False,
            device=None,
        )
    # 已加载, 拿维度和 device 信息
    try:
        device = str(next(model.parameters()).device)  # type: ignore[attr-defined]
    except Exception:
        device = "unknown"
    return BgeStatus(
        configured=bool(settings.bge_model_path),
        model_path=path,
        expected_dim=settings.bge_dim,
        actual_dim=model.get_sentence_embedding_dimension(),
        loaded=True,
        device=device,
    )


@dataclass
class BgeTestResult:
    ok: bool
    message: str
    detail: str | None = None


def test_bge_connection() -> BgeTestResult:
    """embed 一条测试文本看 BGE 是否可用。

    主动触发模型加载 (如果还没加载), 首次测试可能要等 5-10s。
    返向量维度匹配 settings.bge_dim 就算通过。
    """
    from app.services import embedding_service
    try:
        vec = embedding_service.embed_one("建筑能耗测试文本")
    except Exception as e:
        return BgeTestResult(
            ok=False,
            message=f"BGE 推理失败: {e}",
            detail=str(e)[:300],
        )
    if not vec or len(vec) != settings.bge_dim:
        return BgeTestResult(
            ok=False,
            message=f"向量维度异常, 期望 {settings.bge_dim} 实际 {len(vec) if vec else 0}",
        )
    # 取前 3 位让用户看到向量是真实数字, 不展示完整 1024 维占屏
    preview = ", ".join(f"{x:.4f}" for x in vec[:3])
    return BgeTestResult(
        ok=True,
        message=f"embedding 正常, 维度 {len(vec)}",
        detail=f"前 3 维: [{preview}, ...]",
    )


# ---------------------------------------------------------------------------
# 数据源状态 (DataSourceConfig 用)
# ---------------------------------------------------------------------------


@dataclass
class DataSourceStatus:
    """demo 数据源当前状态, 给前端 DataSourceConfig 展示用。"""
    building_count: int          # 当前租户的楼数
    point_count: int             # 当前租户的测点数
    reading_count: int           # 当前租户的读数数
    anomaly_count: int           # 当前租户的异常事件数 (mart.anomaly_event)
    last_seed_at: datetime | None  # 最近一次 bdg2_seed batch 的 finished_at
    last_seed_batch_id: str | None
    last_seed_status: str | None  # LOADING / SUCCEEDED / FAILED


def get_data_source_status(conn, tenant_id: str) -> DataSourceStatus:
    """聚合 demo 数据当前状态 + 最近一次 seed 任务结果。

    三个 count 走当前租户, demo 用户登录看到的就是 demo 租户数据量。
    最近 seed 信息走 ingest.import_batch WHERE dataset_source='bdg2_seed',
    不限租户 (seed 总是写到 demo 租户, 但任何登录用户都能看到上次 seed 时间)。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM core.building WHERE tenant_id = %s",
            (tenant_id,),
        )
        building_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM core.point p JOIN core.building b ON b.id = p.building_id WHERE b.tenant_id = %s",
            (tenant_id,),
        )
        point_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM fact.point_reading WHERE tenant_id = %s",
            (tenant_id,),
        )
        reading_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM mart.anomaly_event WHERE tenant_id = %s",
            (tenant_id,),
        )
        anomaly_count = cur.fetchone()[0]

        # 最近一次 bdg2_seed batch
        cur.execute(
            """
            SELECT id, status, started_at, finished_at
            FROM ingest.import_batch
            WHERE dataset_source = 'bdg2_seed'
            ORDER BY created_at DESC
            LIMIT 1
            """,
        )
        seed_row = cur.fetchone()

    return DataSourceStatus(
        building_count=building_count,
        point_count=point_count,
        reading_count=reading_count,
        anomaly_count=anomaly_count,
        last_seed_at=seed_row[3] if seed_row else None,
        last_seed_batch_id=str(seed_row[0]) if seed_row else None,
        last_seed_status=seed_row[1] if seed_row else None,
    )
