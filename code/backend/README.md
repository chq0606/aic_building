# 后端服务

建筑能耗分析与节能优化平台的后端，FastAPI + psycopg2 + loguru。

## 环境准备

需要本机有 PostgreSQL 15 + pgvector 0.8.3，且数据库 `aic_building` 已建好
（执行 `postgresql_bdg2.sql` 和 `auth_bdg2.sql`）。

```bash
conda activate aic
pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env`，改 `DATABASE_URL` 里的密码：

```bash
cp .env.example .env
# 编辑 .env，把 DATABASE_URL 改成本机实际密码
```

## 启动

```bash
python -m uvicorn app.main:app --reload
```

启动后：

- 健康检查：http://localhost:8000/api/v1/health
- 就绪检查：http://localhost:8000/api/v1/ready
- API 文档（仅 dev 环境）：http://localhost:8000/api/v1/docs

## 目录结构

```
backend/
├── app/
│   ├── main.py              应用入口，挂路由 / CORS / lifespan
│   ├── core/
│   │   ├── config.py        集中配置（pydantic-settings v2）
│   │   ├── logging.py       loguru 配置，控制台 + 文件按天轮转
│   │   └── response.py      统一响应结构 {code, message, data}
│   ├── db/
│   │   └── session.py       psycopg2 ThreadedConnectionPool
│   └── api/
│       └── health.py        /health /ready
├── logs/                    日志输出目录（按天轮转，保留 7 天）
├── requirements.txt
├── .env.example
└── README.md
```

## 验证清单

- [ ] `python -m uvicorn app.main:app --reload` 能启动，无报错
- [ ] `GET /api/v1/health` 返回 200，JSON 含 `status: up`
- [ ] `GET /api/v1/ready` DB 在线时返回 200，DB 挂掉时返回 503
- [ ] `logs/app.log` 文件被创建，控制台和文件日志格式一致
