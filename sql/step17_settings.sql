-- =============================================================================
-- Step 17: 系统设置页 - 用户级配置表
-- =============================================================================
-- 背景:
--   开源自部署, 每个使用者跑一个本地实例。GLM API Key 用户自己配,
--   不能写死在 .env 里 (那会成为全局共享值, 跟"每人配自己的 Key"需求冲突)。
--   一人一租户的前提下, 把 GLM Key 按 user_id 存最合理:
--     - demo 体验账号也能配 (demo 用户试用 AI 助手时需要自己的 Key)
--     - 普通注册用户各配各的, 互不干扰
--
-- 不存 BGE 模型路径:
--   embedding_service 启动时 singleton 加载 BGE 模型, 整个进程共用一个实例,
--   不能按用户切换。BGE 路径仍走 .env (settings.bge_model_path), 前端只读展示。
--
-- 加密策略:
--   glm_api_key_encrypted 存 AES-GCM 密文 (base64 编码) + nonce 拼接。
--   密钥从 settings.jwt_secret 派生 (PBKDF2-HMAC-SHA256, 32 字节)。
--   jwt_secret 本身不入库, 派生函数在 settings_service.py。
--
-- hint 字段:
--   存脱敏提示 (如 "sk-***ab12"), 前端展示用。明文不入库, 但脱敏值可读,
--   让用户在 UI 上能看到"已配置过, 末 4 位是 ab12", 不需要每次解密判断。
--
-- 字段说明:
--   user_id:                  主键, 同时外键到 core."user".id, 用户删了配置级联删
--   glm_api_key_encrypted:    AES-GCM 密文 + nonce, base64 编码。NULL = 未配置
--   glm_api_key_hint:         脱敏提示, 用于前端展示。NULL = 未配置
--   updated_at:               最后修改时间, 前端展示"上次配置时间"
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.user_settings (
    user_id                  uuid PRIMARY KEY REFERENCES core."user"(id) ON DELETE CASCADE,
    glm_api_key_encrypted    text,
    glm_api_key_hint         text,
    updated_at               timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.user_settings IS '用户级配置 (Step 17): GLM API Key 加密存储, 按 user_id 隔离';
COMMENT ON COLUMN core.user_settings.glm_api_key_encrypted IS 'AES-GCM 密文 (base64), 用 jwt_secret 派生密钥加密';
COMMENT ON COLUMN core.user_settings.glm_api_key_hint IS '脱敏提示 (如 sk-***ab12), 前端展示用';
