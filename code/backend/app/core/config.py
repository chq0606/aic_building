"""
集中配置入口。

所有配置项集中在一个 Settings 类里。后续加配置（GLM、BGE、Redis 等）
继续往这个类里加字段，不要散落到各模块自己读环境变量，免得后期找配置项
得到处 grep。

从 .env 文件读，.env 不存在就用字段默认值。pydantic-settings v2 风格：
BaseSettings 从 pydantic_settings 导入，不是 pydantic。
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用基础
    app_name: str = "建筑能耗分析与节能优化平台"
    app_version: str = "1.0.0"
    env: str = "dev"
    host: str = "0.0.0.0"
    port: int = 8000
    # GitHub 仓库链接, AboutPanel 展示用。建了仓库后改 .env APP_REPO_URL 即可。
    # 没填时前端显示"暂未公开"占位, 不展示空链接。
    app_repo_url: str = ""
    app_license: str = "MIT"

    # 日志
    log_level: str = "INFO"
    log_dir: str = "logs"

    # 数据库。DATABASE_URL 是一行式连接串，psycopg2.connect 直接吃。
    # 选这个而不是 PGHOST/PGPORT 五件套，是因为 seed 脚本迁移过来时也走这里，
    # 统一一种写法，避免两套配置并存。
    database_url: str = "postgresql://postgres:postgres@localhost:5432/aic_building"
    db_pool_min: int = 2
    db_pool_max: int = 10
    db_search_path: str = "core, ingest, fact, mart, knowledge, visual, public"

    # CORS。逗号分隔，运行时拆成列表
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # JWT，认证模块用
    jwt_secret: str = "dev_only_change_me_in_production"
    jwt_alg: str = "HS256"
    jwt_expire_hours: int = 24
    jwt_refresh_days: int = 7

    # 智谱 GLM，AI 抽屉用
    glm_api_key: str = ""
    glm_model: str = "glm-4.5"
    # GLM API endpoint, 不带尾斜杠。v4 chat/completions 兼容 OpenAI 格式。
    glm_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    # 单次问答 max_tokens 上限。节能优化场景 JSON 输出比较长, 给 2000 够用。
    glm_max_tokens: int = 2000
    # 温度。节能优化场景 LLM 输出 JSON 要稳, 后端会传 response_format=json_object
    # 并把 temperature 调到 0.3; 普通问答用默认 0.7。
    glm_temperature: float = 0.7
    glm_temperature_json: float = 0.3
    # 流式超时。LLM 推理首字延迟可能 2-5s, 整段生成可能 15-30s, 给 90s 缓冲。
    glm_stream_timeout_seconds: int = 180
    # 非流式调用超时。工具决策阶段用, 包含 LLM 决定调哪些工具的时间。
    # GLM-4.5 + tools 调用输入 token 大 (system + tool schemas + history),
    # 首字延迟 + 完整推理常常 30-60s, 60s 容易爆, 调到 120s。
    glm_request_timeout_seconds: int = 120
    # 连接建立超时 (requests timeout 元组的 connect 部分)。智谱 API 偶尔
    # TCP 握手卡住, 10s 没建连就报错, 不让用户白等。
    glm_connect_timeout_seconds: int = 10
    # 单轮 function call 失败重试次数。GLM 偶尔返格式错的 tool_calls, 重试一次能救回。
    glm_tool_call_retry: int = 1

    # AI 抽屉会话上下文窗口。把最近 N 条消息拼进 LLM 上下文, 太长费 token, 太短失忆。
    assistant_history_window: int = 10
    # 节能优化 JSON 解析失败时的重试次数 (含 prompt 加压提示)。
    # 2 次的依据: attempt=0 常见失败 (校验拒/偶发 120s 超时) 单次重试救回率不满,
    # JSON 模式关 thinking 后单次只要 ~16s, 多一次重试的最坏代价 ~30s 可接受
    assistant_json_retry: int = 2

    # BGE embedding，知识库导入用。空字符串=未启用
    bge_model_path: str = ""
    bge_dim: int = 1024

    # 知识库 PDF 上传目录。相对路径以 backend/ 为根, 绝对路径原样用。
    # 默认放在 backend/storage/knowledge/, 跟代码同级目录, 方便 dev 调试。
    # 生产环境应该挂到独立的存储卷 (NFS / 对象存储), 这里一期不折腾。
    knowledge_upload_dir: str = "storage/knowledge"

    # 三路混检配置 (Step 10)。权重默认 0.5/0.3/0.2 是提示词指定, 在 .env
    # 里可调。归一化前的原始分不在 [0,1] (cosine similarity 在, ts_rank_cd 不在),
    # 融合时 min-max 归一化后才参与加权。
    retrieval_top_k_default: int = 10
    retrieval_top_k_max: int = 50
    retrieval_over_fetch_ratio: int = 3
    retrieval_weight_vector: float = 0.5
    retrieval_weight_keyword: float = 0.3
    retrieval_weight_metadata: float = 0.2
    # BGE 检索 query 侧前缀, 文档侧不加 (见 embedding_service 注释)
    retrieval_bge_query_prefix: str = "为这个句子生成表示以用于检索相关文章: "

    # 体块模式自动估算参数 (Step 11)。building 没设过 visual_model 时,
    # 用 sqm + floors_count 估体块尺寸。层高 3.5m 是国内办公楼均值,
    # 长宽比 1.5:1 是参考 BDG2 demo 数据集建筑 footprint 长宽比均值定的。
    # 后续真实数据多了再调。
    visual_block_floor_height: float = 3.5
    visual_block_aspect_ratio: float = 1.5  # length / width

    # scene API 默认 color metric。eui = 单位面积能耗, 比 total_kwh 跨建筑
    # 比较更合理 (大楼小楼能耗总量不可比)。前端切换 metric 时改这个默认。
    visual_scene_default_metric: str = "eui"

    # 颜色等级阈值 (low/mid/high/critical 四档)。前端按 level 给 BoxGeometry
    # 着色: low 绿/mid 黄/high 橙/critical 红。阈值是按 demo 6 栋楼能耗范围
    # 估的, 真实数据来了要重调。
    # EUI (kWh/m2) 参考 GB 55015-2021: 强制性条文限制 < 80 (低能耗), 这里
    # 取 80/150/250 三档分四档。
    visual_threshold_eui_low: float = 80.0
    visual_threshold_eui_mid: float = 150.0
    visual_threshold_eui_high: float = 250.0
    # total_kwh (kWh) 阈值。demo 月度能耗 1000-30000 kWh, 取
    # 5000/20000/50000 三档。年度能耗会更大, 但 metric 选 total_kwh 时
    # 用户一般会同时传 start/end 限定时间范围, 这里按月度量级估。
    visual_threshold_kwh_low: float = 5000.0
    visual_threshold_kwh_mid: float = 20000.0
    visual_threshold_kwh_high: float = 50000.0
    # anomaly_count 阈值。0 异常 -> low (绿), 1-5 -> mid (黄), 6-20 -> high
    # (橙), > 20 -> critical (红)。0 单独成档因为没异常是健康状态, 跟有
    # 几个异常的 mid 视觉上要分开。
    visual_threshold_anomaly_mid: int = 1
    visual_threshold_anomaly_high: int = 6
    visual_threshold_anomaly_critical: int = 21

    # Redis，重建任务队列的备选方案。一期不启用，留空
    redis_url: str = ""

    # ─── Step 12 TripoSplat 单图重建 ──────────────────────────
    # TripoSplat 推理参数。num_gaussians 越大细节越多但显存涨; steps 越多收敛
    # 越充分但耗时涨。
    #   num_gaussians=32768 + steps=10: 97s/次, 4.6GB 显存, 细节糊 (一栋楼 32K splat)
    #   num_gaussians=131072 + steps=15: ~3 分钟/次, ~6GB 显存, 细节明显提升
    #   num_gaussians=131072 + steps=20: ~4 分钟/次, ~6GB 显存, 收敛更充分细节更锐
    #   num_gaussians=196608 + steps=25: ~6 分钟/次, ~7GB 显存, 1.5x 密度细节更锐 (推荐)
    #   num_gaussians=262144 + steps=20: 8 分钟/次, 撞 8GB 显存上限, 论文默认
    # guidance_scale / shift 是论文默认值, 不动。
    #
    # 预设系统 (Step 13 加): 通过 triposplat_preset 切换两套参数, 避免每次改单个字段。
    #   standard:    131072 + 20 steps (旧默认, 显存安全, 单进程常驻 11.5GB 虚拟内存)
    #   high_quality: 196608 + 25 steps (1.5x 密度, RTX 5060 8GB 显存可跑但峰值更高)
    # 默认走 standard: 本机物理 RAM 仅 16GB, worker 进程常驻 11.5GB, 多一个 pipeline
    # 进程 (verify_step12 / _test_triposplat_inference) 就会把 30GB 虚拟内存打满
    # 触发系统重启 (2026-07-29 14:28 那次黑屏重启就是这个原因, 不是 GPU 显存爆)。
    # high_quality 推理峰值更高, 多进程时更容易爆, 不作为默认。
    # worker 读 preset 决定 steps/num_gaussians, 单独的 triposplat_steps/
    # triposplat_num_gaussians 字段保留做 fallback (preset=none 时直接用)。
    triposplat_preset: str = "standard"
    triposplat_steps: int = 20
    triposplat_num_gaussians: int = 131072
    triposplat_guidance_scale: float = 3.0
    triposplat_shift: float = 3.0
    # 预处理后的 webp 保存质量 (1-100)。PIL 默认 80, 提到 95 减少高频损失
    # (壁板/瓦片/窗格纹理)。质量 95 文件大小约翻倍 (200KB -> 400KB) 但仍远小于
    # 原图, 可接受。
    triposplat_webp_quality: int = 95
    # 5 个权重文件目录。下载脚本默认下到 backend/storage/triposplat/ckpts/。
    # 包含 diffusion_models / vae / clip_vision / background_removal 4 个子目录, 5 个 safetensors。
    triposplat_ckpts_dir: str = "storage/triposplat/ckpts"

    # worker 轮询间隔 (秒)。dev 联调 5s 够快, 生产可调到 30s 减少 DB 压力。
    reconstruction_poll_interval: int = 5
    # 重建产物根目录。job 跑完后 {job_id}/ 下放 output.ply / preview.png /
    # input_photo.* / output_raw.ply / preprocessed.webp。
    reconstruction_storage_dir: str = "storage/reconstruction"
    # 重试上限 + 间隔。3 次 / 60s 是用户已确认的配置, 失败后 PENDING 状态等 60s
    # 再被 worker 拿走, 自然实现"自动重试"不阻塞主循环。
    reconstruction_max_retries: int = 3
    reconstruction_retry_interval_seconds: int = 60

    # 照片上传目录。和 CSV/XLSX 的 data/uploads/ 分开, 避免混。
    photo_upload_dir: str = "storage/uploads"
    # 单张照片大小上限 (10MB)。建筑正脸照片一般 1-5MB, 给个宽松上限。
    photo_max_bytes: int = 10 * 1024 * 1024

    # ─── Step 19 能耗预测 ──────────────────────────────────────
    # Prophet 主力 + LSTM demo + linear baseline, 异步 worker 消费。
    # 跟 Step 12 reconstruction_worker 同一套机制: SELECT FOR UPDATE SKIP LOCKED
    # 轮询 mart.prediction_job 表, 拿 PENDING job -> RUNNING -> SUCCEEDED/FAILED。
    #
    # 装包坑: Prophet 1.1.5 不兼容 numpy 2.x (np.float_ 已删) 且需手动装 CmdStan
    # (Windows 要 C++ 工具链编译)。改用 1.3.0, 自带预编译 CmdStan-2.37.0, 开箱即用。
    # worker 轮询间隔。dev 联调 5s 够快, 生产调 30s 减少 DB 压力。
    prediction_poll_interval: int = 5
    # 最大重试次数。Prophet/LSTM 失败通常是数据问题 (<30 天数据 / 全 NaN),
    # 重试也是同样结果。一期策略: 第一次失败立即置 FAILED, 不自动重试。
    # 保留字段是为了将来扩展 (如 LSTM 偶发 GPU OOM 可重试)。
    prediction_max_retries: int = 3
    prediction_retry_interval_seconds: int = 60
    # 训练历史数据上限 (天)。BDG2 demo 1 年 = 365 天, 真实客户多年数据截到最近 365 天。
    # 超过 365 天的训练数据 Prophet 学得到年季节性更准, 但训练时间线性增长,
    # 365 天是 demo 体验和预测精度的平衡点。
    prediction_train_max_days: int = 365
    # MAPE 验证集天数 (最后 N 天实际值 vs 预测算 MAPE)。
    # 7 天是因为预测 horizon 默认 7-30 天, 用同等长度的"留出集"评估。
    prediction_mape_eval_days: int = 7
    # LSTM 模型参数 (提示词指定: input=1, hidden=32, layers=2, output=1)。
    # 纯 CPU 跑, batch_size=8 + window=14 控制参数量, 单楼训练目标 30-60s。
    prediction_lstm_epochs: int = 50
    prediction_lstm_window: int = 14
    prediction_lstm_hidden: int = 32
    prediction_lstm_layers: int = 2
    prediction_lstm_batch_size: int = 8
    prediction_lstm_learning_rate: float = 0.01
    # 单楼预测最长 horizon (天), 超过拒绝。提示词要求 1-90, 这里和 SQL CHECK 对齐。
    prediction_max_horizon_days: int = 90

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS 配置在 .env 里是逗号分隔字符串，这里拆成列表给 FastAPI 用。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def log_dir_path(self) -> Path:
        """日志目录。相对路径以 backend/ 为根，绝对路径原样用。"""
        p = Path(self.log_dir)
        if not p.is_absolute():
            p = BACKEND_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def knowledge_upload_path(self) -> Path:
        """知识库 PDF 存储目录。相对路径以 backend/ 为根。"""
        p = Path(self.knowledge_upload_dir)
        if not p.is_absolute():
            p = BACKEND_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def reconstruction_storage_path(self) -> Path:
        """重建产物存储目录。job 跑完后 {job_id}/ 下放 4 个产物文件。"""
        p = Path(self.reconstruction_storage_dir)
        if not p.is_absolute():
            p = BACKEND_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def photo_upload_path(self) -> Path:
        """照片上传目录。和 CSV 的 data/uploads/ 分开。"""
        p = Path(self.photo_upload_dir)
        if not p.is_absolute():
            p = BACKEND_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def triposplat_ckpts_path(self) -> Path:
        """TripoSplat 5 个权重文件目录。"""
        p = Path(self.triposplat_ckpts_dir)
        if not p.is_absolute():
            p = BACKEND_ROOT / p
        return p


@lru_cache
def get_settings() -> Settings:
    """单例。lru_cache 保证全局只构造一次，避免每次请求都重读 .env。"""
    return Settings()


settings = get_settings()
