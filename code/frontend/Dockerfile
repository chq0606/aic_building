# ============================================================================
# frontend/Dockerfile - 多阶段构建 Vue3 + Vite 前端
# ----------------------------------------------------------------------------
# Stage 1 (builder): node:20-slim + pnpm 装依赖 + vite build 产物 dist/
# Stage 2 (runtime): nginx:alpine + 复制 dist/ + nginx.conf 配置 SPA + /api 反代
#
# 产物镜像 ~30MB, nginx 静态托管, /api/v1 反代到 backend:8000
# ============================================================================

# ─── Stage 1: 构建 ──────────────────────────────────────────────────────────
# pnpm@11 需要 Node 22 (node:20 缺内置模块会报 ERR_UNKNOWN_BUILTIN_MODULE)
FROM node:22-slim AS builder

WORKDIR /build

# 先装依赖 (利用 docker layer cache, code 变了不重装 node_modules)
COPY code/frontend/package.json code/frontend/pnpm-lock.yaml code/frontend/pnpm-workspace.yaml ./

# 启用 corepack 让 pnpm 版本锁生效
RUN corepack enable && corepack prepare pnpm@11.15.0 --activate \
    && pnpm install --frozen-lockfile --ignore-scripts

# 复制源码 + 构建
COPY code/frontend/ ./

# 跳过 pnpm 的 approve-builds 交互 (memory: 前端绕过 pnpm approve-builds)
ENV PNPM_APPROVE_BUILDS=1
RUN pnpm build

# ─── Stage 2: nginx 静态托管 ──────────────────────────────────────────────
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /build/dist /usr/share/nginx/html

# 复制 nginx 配置 (SPA fallback + /api 反代 + cesium 长缓存)
COPY code/frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# nginx 默认 daemon off, 容器退出时 nginx 也退出
CMD ["nginx", "-g", "daemon off;"]
