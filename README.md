# 建筑能耗分析与节能优化平台 (AIC Building)

> 面向园区 / 校园建筑群的能耗数据平台：把零散的逐时电表读数，变成「可视化 → 异常发现 → 能耗预测 → AI 节能建议」的完整决策链路。

## 效果预览

![园区探索页](图片/运行图片/园区探索页.png)

## 核心能力

| 能力 | 说明 |
|------|------|
| **园区 3D 可视化** | Cesium 三维地图 + 建筑体块按能耗着色，点选楼栋查看能耗 / 异常 / 元数据 |
| **能耗预测** | Prophet（主力）/ LSTM（demo）/ Linear（基线）三模型对比，异步 worker 训练 + MAPE 评估 |
| **异常检测** | Z-score + 滑动窗口自动识别能耗异常，带证据链 |
| **知识库问答** | PDF 导入（PyMuPDF + EasyOCR 扫描件）+ BGE 向量化 + 向量 / 关键词 / 元数据三路混检 |
| **AI 节能助手** | 智谱 GLM 流式问答 + function call 自动调工具 + 节能方案结构化输出 |
| **单图 3D 重建** | TripoSplat 单图重建 + Cesium 3D Tiles + 原生 3DGS 渲染 |

## 快速开始（Docker，10 分钟跑通）

### 前置要求

- **Docker** 24.0+（含 Docker Compose v2）
- **Git** 2.30+
- （可选）NVIDIA GPU + nvidia-container-toolkit，跑单图重建用（没 GPU 也能用其他功能）
- （可选）智谱 GLM API Key，跑 AI 抽屉用（不填则该功能返诊断错误）

### 启动

```bash
git clone <repo_url>
cd aic_building

# 1. 配置环境变量（至少改 JWT_SECRET，有 GLM_API_KEY 就填）
cp .env.example .env

# 2. 起 4 个服务（postgres + backend + prediction_worker + frontend）
make up

# 3. 灌 BDG2 demo 数据（6 栋楼 / 1 年 / ~17 万条逐时读数）
make seed-demo

# 4. 浏览器打开 http://localhost:8080，账号 demo / demo123
```

### 启用单图 3D 重建（GPU）

```bash
make download-ckpts   # 下载 TripoSplat 5 个权重 (~3.6GB)
make gpu-up           # 多起 triposplat_worker 容器
```

> 想快速体验单图重建？用项目自带样例照片 [图片/重建图片/3d2.jpg](图片/重建图片/3d2.jpg)：demo 登录后进「数据接入 → 3D 建模」上传它、填好尺寸即可提交重建 job。

## 技术栈

| 层 | 技术 |
|----|------|
| 数据底座 | PostgreSQL 15 + pgvector（5 schema 分层：core / ingest / fact / mart / knowledge） |
| 后端 | FastAPI + pydantic-settings + psycopg2 连接池 + loguru |
| 前端 | Vue 3 + TypeScript + Vite + Pinia + Ant Design Vue 4 + SCSS tokens |
| 可视化 | ECharts（图表）+ Cesium 1.122（3D 体块 / 高斯泼溅） |
| AI / ML | 智谱 GLM-4.5（问答 / 工具调用）、BGE embedding（向量检索）、Prophet / LSTM / Linear（预测）、TripoSplat（3D 重建） |
| OCR | EasyOCR + PyMuPDF |

## 目录结构

```
aic_building/
├── sql/                      # 增量 SQL + 合并后的 init.sql（docker 首启自动执行）
├── code/
│   ├── seed_bdg2.py          # BDG2 demo 数据导入脚本
│   ├── backend/              # FastAPI 后端 + prediction / triposplat 两个 worker
│   └── frontend/             # Vue 3 前端（含 nginx 部署配置）
├── extracted_data/           # BDG2 demo 种子数据（6 栋楼 2017 年子集）
├── tools/merge_sql.py        # 增量 SQL → init.sql 合并脚本
├── docker-compose.yml        # 服务编排
├── Makefile                  # up / seed-demo / test / download-ckpts 等
└── .env.example              # 环境变量样例
```

## 项目背景

本项目源于第八届全球校园人工智能算法精英大赛「AI 场景建造」创新赛的参赛作品，围绕建筑能耗的采集、可视化、预测与 AI 节能优化展开。

## 本地开发（不用 Docker）

```bash
# 后端
conda create -n aic python=3.11
conda activate aic
pip install torch --index-url https://download.pytorch.org/whl/cpu   # GPU 场景走 cu130
pip install -r code/backend/requirements.txt
cd code/backend
python -m uvicorn app.main:app --reload            # http://localhost:8000

# 前端
cd code/frontend
pnpm install --ignore-scripts
pnpm dev                                            # http://localhost:5173
```

## 测试

```bash
make test                        # 后端 pytest（容器内）

# 或本机直跑
cd code/backend && pytest tests/ -v
cd code/frontend && npx vitest run
```

## 常见问题

- **Windows 上 `make up` 慢 / postgres 容器卡**：用 WSL2 + Docker Desktop，NTFS 直挂数据卷慢约 10 倍
- **没有 GPU**：不启 `--profile gpu`，单图重建 job 停在 PENDING，其他功能正常
- **BGE 模型首次下载慢**：设 `HF_ENDPOINT=https://hf-mirror.com` 走国内镜像
- **没填 GLM API Key**：AI 抽屉能打开、发消息返「GLM_API_KEY 未配置」，其他功能不受影响
- **prophet 装不上**：用 `prophet==1.3.0`（自带预编译 CmdStan，兼容 numpy 2.x）

## AI 辅助开发说明

本项目在开发过程中使用 AI 编程助手（Claude Code）辅助实现代码，技术选型、架构设计、需求评审、代码审查与验收均由人工完成。开发过程中使用的提示词见 [构建思路/提示词.txt](构建思路/提示词.txt) 与 [构建思路/提示词1.txt](构建思路/提示词1.txt)。

## 许可证

本项目代码以 [MIT License](LICENSE) 开源：任何人可自由使用、修改、分发，仅需保留版权声明。

**第三方资源声明**（各自遵循其原始许可，不包含在本项目 MIT 授权内）：

| 资源 | 用途 | 是否入仓库 |
|------|------|-----------|
| BDG2 数据集（[Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)） | demo 种子数据 | ✅ `extracted_data/`（6 栋楼 2017 子集 ~17 万条） |
| TripoSplat 权重（VAST-AI） | 单图 3D 重建 | ❌ `make download-ckpts` 单独下载 |
| BGE-M3 模型（BAAI） | 知识库向量化 | ❌ 首次运行自动下载 |
| 国标 / 行业标准 PDF（GB 55015 等） | 知识库演示 | ❌ 有版权，用户自行准备 |