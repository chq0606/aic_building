# 建筑能耗分析与节能优化平台 (AIC Building)

> 基于 PostgreSQL + pgvector + Vue3 + FastAPI 的建筑能耗分析平台, 集成能耗预测 / 异常检测 / 知识库 / AI 抽屉 / 3D 重建全栈能力.

## 效果预览

![园区探索页](图片/运行图片/园区探索页.png)

## 项目亮点

- **数据底座** PostgreSQL 15 + pgvector 向量检索, 5 schema 分层 (core / ingest / fact / mart / knowledge)
- **能耗预测** Prophet 主力 + LSTM demo + linear baseline, 异步 worker 跑训练 + MAPE 评估
- **异常检测** Step 08, 基于 Z-score + 滑动窗口的能耗异常自动识别 + 证据链
- **知识库** Step 09-10, PDF 导入 + PyMuPDF 解析 + EasyOCR 扫描件 + BGE embedding + 三路混检 (向量/关键词/元数据)
- **AI 抽屉** Step 18, 智谱 GLM-4.5 流式响应 + function call 工具集 + 节能优化 JSON 结构化输出
- **3D 重建** Step 12, TripoSplat 单图重建 + Cesium 3D Tiles + Box 体块能耗着色
- **设计系统** Vue3 + AntD 4 + SCSS tokens, 暖灰琥珀色板, 8px 网格, JWT 全 localStorage

## 一键启动 (10 分钟跑通)

### 前置要求

- **Docker** 24.0+ (含 Docker Compose v2)
- **Git** 2.30+
- (可选) NVIDIA GPU + nvidia-container-toolkit, 用于 Step 12 单图重建 (没 GPU 也能跑其他功能)
- (可选) 智谱 GLM API Key, 用于 AI 抽屉 (留空则 AI 抽屉返诊断错误, 其他功能不受影响)

### 启动步骤

```bash
git clone <repo_url>
cd aic_building

# 1. 配置环境变量
cp .env.example .env
# 编辑 .env, 至少改 JWT_SECRET (生产环境必改), 有 GLM_API_KEY 就填上

# 2. 启动 5 个服务 (postgres + backend + prediction_worker + frontend, + triposplat_worker 需 --profile gpu)
make up

# 3. 灌 BDG2 demo 数据 (6 栋楼 / 1 年能耗 / ~17 万条 hourly 读数)
make seed-demo

# 4. 浏览器打开
# http://localhost:8080
# 登录: demo / demo123
```

### 启用 GPU 重建 (Step 12)

```bash
# 1. 下载 TripoSplat 5 个权重 (~3.6GB, 首次跑耗时 5-30min)
make download-ckpts

# 2. 用 --profile gpu 启动 (会多启 triposplat_worker 容器)
make gpu-up
```

> 想快速体验单图重建？可直接用项目自带样例照片 [`图片/重建图片/3d2.jpg`](图片/重建图片/3d2.jpg)：登录 demo 后进「数据接入 → 3D 建模」tab，上传它、填好尺寸即可提交重建 job 玩一下。

## 技术栈

| 层 | 技术 | 版本 |
|----|------|------|
| 数据库 | PostgreSQL + pgvector | 15 + 0.8.3 |
| 后端 | FastAPI + pydantic + loguru + psycopg2 | 0.115.6 + 2.10 + 0.7.3 + 2.9.10 |
| 前端 | Vue 3 + TypeScript + Vite + Pinia + AntD 4 | 3.5 + 5.6 + 6.0 + 2.2 + 4.2 |
| 图表 | ECharts 5.5 + Cesium 1.122 | (3D 体块 + 折线 + 雷达) |
| AI | 智谱 GLM-4.5 + BGE embedding + Prophet 1.3.0 + PyTorch LSTM | (流式 + embedding + 预测) |
| OCR | EasyOCR 1.7.2 + PyMuPDF 1.28 | (扫描件 PDF 识别) |
| 重建 | TripoSplat + nvidia/cuda 12.4.1 + cu130 torch | (单图 -> 3D PLY) |

## 目录结构

```
aic_building/
├── sql/
│   ├── init.sql               # 合并后的 SQL (19 个增量 SQL), docker 首次启动自动执行
│   └── step*.sql / *_bdg2.sql # 增量 SQL 源文件
├── docker-compose.yml        # 5 服务编排 (postgres/backend/2 workers/frontend)
├── Makefile                  # up/down/migrate/seed-demo/test/logs/clean/download-ckpts
├── .env.example              # 环境变量样例
│
├── code/
│   ├── seed_bdg2.py          # BDG2 demo 数据导入脚本
│   ├── backend/              # FastAPI 后端 + 2 个 worker
│   │   ├── app/
│   │   │   ├── api/          # 16 个路由模块
│   │   │   ├── core/         # config / deps / security / response / logging
│   │   │   ├── db/           # psycopg2 连接池
│   │   │   ├── models/       # Pydantic 请求/响应模型
│   │   │   ├── services/     # 业务服务层 (query/anomaly/prediction/...)
│   │   │   ├── seed/         # demo 用户初始化
│   │   │   └── main.py       # FastAPI 入口
│   │   ├── worker/           # 异步 worker
│   │   │   ├── prediction_worker.py  # Step 19 能耗预测
│   │   │   └── triposplat_worker.py  # Step 12 3D 重建
│   │   ├── tests/            # pytest 7 个测试文件
│   │   ├── scripts/          # download_triposplat_ckpts.sh 等
│   │   ├── storage/          # 上传文件 / 模型权重 (gitignored)
│   │   ├── Dockerfile        # CPU 镜像 (backend + prediction_worker)
│   │   ├── Dockerfile.triposplat  # GPU 镜像 (triposplat_worker)
│   │   ├── requirements.txt  # Python 依赖锁版本
│   │   └── verify_step*.py   # Step 01-19 验收脚本 (冒烟测试)
│   └── frontend/
│       ├── src/
│       │   ├── api/           # axios + JWT 拦截器
│       │   ├── components/    # 业务组件 (analysis/park/ai-drawer/...)
│       │   ├── composables/   # 共享组合式函数
│       │   ├── stores/        # Pinia (auth/context/ui)
│       │   ├── styles/        # tokens.scss + echarts-theme
│       │   ├── views/         # 7 个页面 (Login/Park/Analysis/DataHub/Settings/...)
│       │   ├── __tests__/     # vitest 3 个测试文件
│       │   ├── router/
│       │   ├── App.vue
│       │   └── main.ts
│       ├── Dockerfile        # multi-stage (node build + nginx runtime)
│       ├── nginx.conf        # SPA + /api 反代 + cesium 缓存
│       ├── vitest.config.ts
│       └── package.json
│
├── extracted_data/          # BDG2 demo CSV 数据 (metadata/readings_2017/weather_2017)
├── tools/
│   └── merge_sql.py          # 19 个增量 SQL 合并成 sql/init.sql
├── data/postgres/           # PostgreSQL 数据持久化 (gitignored)
└── 工作记录_*.md            # 开发日志 (Step 01-20)
```

## 核心功能

| Step | 模块 | 入口接口 | 说明 |
|------|------|----------|------|
| 01 | 后端骨架 | GET /api/v1/health | FastAPI + psycopg2 + loguru |
| 02 | 认证 | POST /api/v1/auth/{register,login,refresh} | JWT + bcrypt + 一人一租户 |
| 03 | 数据库 | (init.sql 自动跑) | pgvector + 5 schema 分层 |
| 04 | demo 用户 | (启动时自动 ensure_demo_user) | demo / demo123 只读 |
| 05 | 上传 | POST /api/v1/uploads/sessions | CSV/XLSX 宽表转长表 |
| 06 | 导入 | POST /api/v1/imports/batches/{id}/commit | staging -> fact |
| 07 | 数据质量 | GET /api/v1/data-quality/overview | 完整性/及时性/异常率 |
| 08 | 异常检测 | GET /api/v1/anomalies/overview | Z-score + 滑动窗口 |
| 09 | 知识库导入 | POST /api/v1/knowledge/documents | PyMuPDF + EasyOCR + BGE |
| 10 | 三路混检 | POST /api/v1/knowledge/search | 向量/关键词/元数据 |
| 11 | 体块模型 | GET /api/v1/buildings/{id}/visual-models | 自动估体块 |
| 12 | 3D 重建 | POST /api/v1/buildings/{id}/reconstruction/single-photo | TripoSplat GPU |
| 14 | 设置 | GET /api/v1/settings/* | 用户偏好持久化 |
| 15 | 分析中心 | GET /api/v1/query/* | 7 个 ECharts 图表 |
| 17 | 用户设置 | (合并到 14) | UI 偏好 (主题/默认指标) |
| 18 | AI 抽屉 | POST /api/v1/assistant/sessions + /messages/stream | GLM-4.5 流式 + 工具集 |
| 19 | 能耗预测 | POST /api/v1/prediction/buildings/{id}/forecast | Prophet + LSTM + linear |
| 20 | 部署打包 | docker compose up | 一键启动 + 测试 |

## 开发指南

### 本地开发 (不用 Docker)

```bash
# 后端
cd code/backend
conda create -n building_aic python=3.11
conda activate building_aic
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
# 起 PostgreSQL (本机或 docker compose up postgres)
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/aic_building
python -m uvicorn app.main:app --reload

# 前端
cd code/frontend
pnpm install --ignore-scripts
pnpm dev  # http://localhost:5173
```

### 跑测试

```bash
# Docker 模式
make test

# 本机直跑 (后端)
cd code/backend
pytest tests/ -v

# 本机直跑 (前端)
cd code/frontend
npx vitest run
```

### 重新生成 init.sql

```bash
# 改了某个 step SQL 后重跑合并
python tools/merge_sql.py
# 然后 make clean && make up 让 postgres 容器重跑 init.sql
```

## 验收清单

- [ ] `make up && make seed-demo` 跑通
- [ ] 浏览器 http://localhost:8080 看到 demo / demo123 登录
- [ ] demo 登录看到 6 栋楼 demo 数据
- [ ] /analysis 页面 7 个 ECharts 图表全部渲染
- [ ] /park 页面 Cesium 3D 体块着色正常
- [ ] /prediction 面板能提交预测 job (Prophet/LSTM/Linear 任选)
- [ ] AI 抽屉 (右下角浮动按钮) 能打开 + 发消息 (需 GLM_API_KEY)
- [ ] `make test` 后端 pytest + 前端 vitest 全过

## 常见问题

**Q: Windows 上 `make up` 慢 / postgres 容器卡住?**
A: 用 WSL2 + Docker Desktop, NTFS 直挂数据卷性能差 10 倍.

**Q: 没有 NVIDIA GPU, Step 12 重建功能怎么办?**
A: 不启 `--profile gpu`, triposplat_worker 不启动, 其他功能正常. 提交重建 job 会停在 PENDING 状态.

**Q: BGE embedding 模型首次启动下载慢?**
A: 设环境变量 `HF_ENDPOINT=https://hf-mirror.com` 走国内镜像. 模型 ~1GB, 首次下完后缓存.

**Q: 智谱 GLM API Key 没填, AI 抽屉能打开吗?**
A: 能打开, 但发消息会返 "GLM_API_KEY 未配置" 诊断错误. 其他功能不受影响.

**Q: prophet 装不上?**
A: 用 prophet==1.3.0 (自带预编译 CmdStan-2.37.0). 老版 1.1.5 不兼容 numpy 2.x 且要本地编译 CmdStan.

## AI 辅助开发说明

本项目在开发过程中使用 AI 编程助手（Claude Code）辅助实现，架构设计、需求评审、代码审查与验收均由人工完成。

## 许可证

本项目代码以 [MIT License](LICENSE) 开源：任何人可自由使用、修改、分发，只需保留版权声明。

**第三方资源声明**（各自遵循其原始许可，不包含在本项目 MIT 授权内）：

| 资源 | 用途 | 是否入仓库 |
|------|------|-----------|
| BDG2 数据集 ([Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)) | demo 种子数据 | ✅ `extracted_data/` 6 栋楼 2017 年子集 (~17 万条) |
| TripoSplat 重建权重 (VAST-AI) | 单图 3D 重建 | ❌ `make download-ckpts` 单独下载 |
| BGE-M3 embedding 模型 (BAAI) | 知识库向量化 | ❌ 首次运行自动下载 |
| 国标 / 行业标准 PDF (GB 55015 等) | 知识库演示 | ❌ 有版权, 用户自行准备 |
