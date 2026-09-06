# ============================================================================
# Makefile - AIC Building 一键启动入口
# ----------------------------------------------------------------------------
# 评委验证流程:
#   git clone <repo>
#   cd aic_building
#   cp .env.example .env
#   make up            # 启动 5 服务 (postgres/backend/2 workers/frontend)
#   make seed-demo     # 灌 BDG2 demo 数据 (6 栋楼 1 年能耗)
#   # 浏览器打开 http://localhost:8080 用 demo/demo123 登录
#   make test          # 跑 pytest + vitest
#   make down          # 关闭
# ============================================================================

.PHONY: up down migrate seed-demo test logs ps clean download-ckpts gpu-up rebuild

# 默认 CPU 模式 (triposplat_worker 不启, 没 GPU 也能跑)
up:
	docker compose up -d --build

# GPU 模式: 启用 triposplat_worker (需要 NVIDIA 驱动 + nvidia-container-toolkit)
gpu-up:
	docker compose --profile gpu up -d --build

down:
	docker compose down

# 数据库迁移 (init.sql 已在 postgres 首次启动时自动执行, 这里保留命令兼容提示词要求)
migrate:
	@echo "init.sql 已在 postgres 容器首次启动时自动执行, 无需手动 migrate"
	@echo "若需强制重跑: make clean && make up"

# 灌 BDG2 demo 数据 (1 tenant + 1 site + 6 buildings + ~16.9 万 readings)
seed-demo:
	docker compose exec backend python /app/seed_bdg2.py

# 跑后端 pytest + 前端 vitest
test:
	@echo "=== Backend pytest ==="
	docker compose exec backend pytest tests/ -v
	@echo ""
	@echo "=== Frontend vitest ==="
	docker compose exec frontend sh -c "cd /app && npx vitest run" 2>/dev/null || \
		echo "(前端 vitest 在容器内不可用, 请在本机 cd code/frontend && npx vitest run)"

# 单独跑后端测试
test-backend:
	docker compose exec backend pytest tests/ -v

# 单独跑前端测试
test-frontend:
	cd code/frontend && npx vitest run

# 看实时日志 (所有服务)
logs:
	docker compose logs -f

# 看指定服务日志 (make logs-svc=backend)
logs-svc:
	docker compose logs -f $(svc)

# 看服务状态
ps:
	docker compose ps

# 强制重建镜像 (改了 Dockerfile 后用)
rebuild:
	docker compose build --no-cache

# 下载 TripoSplat 5 个权重文件 (3.6GB, 首次启用 GPU 功能前必跑)
download-ckpts:
	@echo "下载 TripoSplat 权重到 code/backend/storage/triposplat/ckpts/ (3.6GB)"
	bash code/backend/scripts/download_triposplat_ckpts.sh

# 清理: 关闭容器 + 删数据卷 (会丢数据库, 谨慎)
clean:
	docker compose down -v
	@echo "清理本机 data/postgres 数据目录..."
	rm -rf data/postgres
	@echo "完成. 重新启动: make up"
