-- Step 12: 重建任务表扩展补丁
--
-- 给 core.reconstruction_job 表加 8 列:
--   retry_count        重试计数, worker 自动重试用
--   last_retry_at      上次重试时间, 隔 60s 防止瞬间重试
--   input_length_m     输入参数: 建筑长度, 用于尺度校准
--   input_width_m      输入参数: 建筑宽度
--   input_height_m     输入参数: 建筑总高
--   input_floors_count 输入参数: 楼层数
--   input_position_x   输入参数: 园区内 X 坐标, 可空
--   input_position_y   输入参数: 园区内 Y 坐标, 可空
--
-- 输入参数存 job 表的原因: worker 进程跟 API 进程独立, 用 DB 传参
-- 比从其他表 join 拿简单 (photo 已用 input_upload_file_id 字段存 uuid, 不写 FK)。
--
-- 加部分索引只为 PENDING 行: 轮询查询走 WHERE status='PENDING'
-- AND last_retry_at + interval '60s' < now(), 索引覆盖。SUCCEEDED/FAILED/
-- RUNNING 行不进索引, 不占空间。

ALTER TABLE core.reconstruction_job
    ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_retry_at timestamptz,
    ADD COLUMN IF NOT EXISTS input_length_m numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_width_m numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_height_m numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_floors_count integer,
    ADD COLUMN IF NOT EXISTS input_position_x numeric(10,2),
    ADD COLUMN IF NOT EXISTS input_position_y numeric(10,2);

CREATE INDEX IF NOT EXISTS idx_recon_job_pending
    ON core.reconstruction_job (status, last_retry_at)
    WHERE status = 'PENDING';
