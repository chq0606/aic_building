# ============================================================================
# backend/Dockerfile - CPU 镜像 (给 backend + prediction_worker 共用)
# ----------------------------------------------------------------------------
# 多阶段构建:
#   Stage 1 (builder): 装 Python 依赖到 /root/.local, 避免最终镜像带编译工具链
#   Stage 2 (runtime): python:3.11-slim + 复制 site-packages + 源码
#
# torch 走 CPU 版 (200MB), 不默认 pip install torch (拉 CUDA 版 2GB+).
# Prophet 1.3.0 自带预编译 CmdStan-2.37.0 (prophet/stan_model/), 在 python:3.11-slim
# 上跑需 glibc + libstdc++, slim 镜像自带, 不用额外装编译器.
#
# 复用对象:
#   - backend (FastAPI 主服务)
#   - prediction_worker (Step 19 Prophet/LSTM 跑 CPU 够用, demo 60s/楼)
#
# triposplat_worker 走 Dockerfile.triposplat (GPU 版, 单独构建)
# ============================================================================
FROM python:3.11-slim AS builder

# 系统依赖: build-essential 编译 C 扩展, libgl1 + libglib2.0-0 给 easyocr 用
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY code/backend/requirements.txt ./

# 先装 torch CPU 版 (走官方 CPU 索引, 不走 PyPI 默认 CUDA 版)
# 再装 requirements.txt (其他依赖, 不含 torch, 会复用已装的 torch)
# 用清华镜像加速 pip, 国内拉包快
RUN pip install --user --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install --user --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: 运行时 (最薄一层)
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# 运行时系统库: libgl1 (easyocr) + libpq5 (psycopg2) + libglib2.0-0
# weasyprint 依赖 libpango/libgio, Dockerfile 不装, weasyprint 在容器里跑导出 PDF
# 会失败 (一期 AI 抽屉导出 PDF 是次要功能, README 标注限制)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 复制 builder 装好的 Python 包 (site-packages + 入口脚本)
COPY --from=builder /root/.local /root/.local

WORKDIR /app
# 复制后端源码 (COPY 是 docker build context 的相对路径, context 是项目根 aic_building/)
COPY code/backend/ /app/

ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1

# 健康检查: 每 30s 调 /api/v1/health, 3 次失败标记 unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health').status==200 else 1)" || exit 1

EXPOSE 8000

# 默认启动 backend, prediction_worker 服务在 docker-compose 里 override command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
