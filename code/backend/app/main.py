"""
FastAPI 应用入口。

启动顺序：日志 → 连接池 → 路由 → CORS。
退出时关连接池。

跑法：
    cd backend
    python -m uvicorn app.main:app --reload

--reload 时 uvicorn 会重新 import 本模块，lifespan 里的 init/close
会跟着重跑，连接池不会泄漏。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api import anomaly, assistant, auth, data_quality, floor, health, imports, knowledge, prediction, query, reconstruction, retrieval, seed, sites, upload, visual
# settings 模块名跟 app.core.config 的 Settings 实例 (settings) 同名冲突,
# 这里起别名 settings_api 避开覆盖。
from app.api import settings as settings_api
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import init_pool, close_pool
from app.seed.demo_user import ensure_demo_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("启动 {} v{}（env={}）", settings.app_name, settings.app_version, settings.env)
    init_pool()
    # 启动时确保 demo 体验账号存在,失败不阻断启动(不影响其他用户登录)
    try:
        ensure_demo_user()
    except Exception as e:
        logger.warning("demo 用户初始化失败,不影响启动: {}", e)
    logger.info("所有路由：health=/api/v1/health, /api/v1/ready, auth=/api/v1/auth/*, uploads=/api/v1/uploads/*, imports=/api/v1/imports/*, data-quality=/api/v1/data-quality/*, query=/api/v1/query/*, anomalies=/api/v1/anomalies/*, knowledge=/api/v1/knowledge/*, retrieval=/api/v1/knowledge/search, sites=/api/v1/sites, visual=/api/v1/buildings/*/visual-models, /api/v1/sites/*/scene, reconstruction=/api/v1/buildings/*/reconstruction/single-photo, /api/v1/reconstruction/jobs/*, settings=/api/v1/settings/*, assistant=/api/v1/assistant/*, prediction=/api/v1/prediction/*")
    yield
    close_pool()
    logger.info("应用已退出")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    # 生产环境关掉 /docs，开发环境留着方便联调
    docs_url="/api/v1/docs" if settings.env == "dev" else None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json" if settings.env == "dev" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 所有业务路由挂在 /api/v1 前缀下，方便后续版本演进
app.include_router(health.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(seed.router, prefix="/api/v1")
app.include_router(upload.router, prefix="/api/v1")
app.include_router(imports.router, prefix="/api/v1")
app.include_router(data_quality.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(anomaly.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(retrieval.router, prefix="/api/v1")
app.include_router(visual.router, prefix="/api/v1")
app.include_router(sites.router, prefix="/api/v1")
app.include_router(reconstruction.router, prefix="/api/v1")
app.include_router(settings_api.router, prefix="/api/v1")
app.include_router(assistant.router, prefix="/api/v1")
app.include_router(prediction.router, prefix="/api/v1")
app.include_router(floor.router, prefix="/api/v1")
