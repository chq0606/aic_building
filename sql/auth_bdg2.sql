-- ============================================================
-- 认证模块建表 SQL（Step 02 注册 + 登录）
-- 项目：建筑能耗分析与节能优化平台
-- 说明：注册 + 登录 + JWT，一人一租户，不做 RBAC
-- 依赖：postgresql_bdg2.sql 已执行（core.tenant 已存在）
--
-- 本文件只建表，不插数据。
-- 注册用户的账号由后端 Step 02 注册 API 创建。
-- demo 用户由后端 Step 03 seed 时顺便创建（demo / demo123）。
--
-- 类型对齐：所有 id 字段统一用 uuid（与 postgresql_bdg2.sql 一致）
-- ============================================================

SET client_encoding = 'UTF8';

-- ── 1. core."user" 用户表 ───────────────────────────────────
-- 一人一租户：每个用户绑一个 tenant，注册时后端自动创建 tenant
-- id / tenant_id 都是 uuid，对齐 postgresql_bdg2.sql 的风格
DROP TABLE IF EXISTS core."user" CASCADE;

CREATE TABLE core."user" (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    username        text NOT NULL UNIQUE,        -- 全局唯一（跨所有租户）
    password_hash   text NOT NULL,               -- bcrypt 哈希
    display_name    text,
    email           text,
    is_active       boolean NOT NULL DEFAULT true,
    is_demo         boolean NOT NULL DEFAULT false,  -- demo 用户标记，只读
    last_login_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_user_tenant ON core."user" (tenant_id);
CREATE INDEX idx_user_username ON core."user" (username);

COMMENT ON TABLE core."user" IS '系统用户表，一人一租户，is_demo 标记 demo 只读账号';
COMMENT ON COLUMN core."user".password_hash IS 'bcrypt 哈希，不存明文';
COMMENT ON COLUMN core."user".is_demo IS 'demo 用户标记，业务 API 检测到则禁止写操作';

-- ── 2. updated_at 自动更新触发器 ────────────────────────────
CREATE OR REPLACE FUNCTION core.touch_user_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_user_updated_at ON core."user";
CREATE TRIGGER trg_user_updated_at
    BEFORE UPDATE ON core."user"
    FOR EACH ROW
    EXECUTE FUNCTION core.touch_user_updated_at();

-- ============================================================
-- 执行完毕
-- 验证：SELECT COUNT(*) FROM core."user";  -- 应返回 0
--
-- 后端 Step 02 注册 API 逻辑：
--   1. INSERT core.tenant (tenant_code, tenant_name) VALUES ('user_'+username, username)
--   2. INSERT core."user" (tenant_id, username, password_hash, ...) VALUES (新tenant.id, ...)
--   3. 返回 JWT
--
-- 后端 Step 03 seed 逻辑：
--   1. 检测 core."user" 是否存在 username='demo'
--   2. 不存在则 INSERT，is_demo=true，password_hash = bcrypt('demo123')
-- ============================================================
