-- Step 11 体块模式后端 schema 补丁: 给 core.building_visual_model 加
-- position_x / position_y 两列。
--
-- 为什么需要这两个字段: 提示词1.txt Step 11 的 POST /buildings/{id}/visual-models/block
-- body 明确要求带 position_x / position_y, 是园区内的相对坐标 (米)。原表
-- (postgresql_bdg2.sql:278-292) 只有 length_m / width_m / height_m 三个尺寸字段,
-- 没有任何字段表达"楼在园区哪个位置"。scene API 一次返回所有 building 的
-- position 给前端 CesiumJS 渲染, 没这个字段没法定位。
--
-- 为什么 nullable: 旧的 visual_model 记录没 position 数据, backfill 没意义。
-- 自动估算的体块 (building 没设过 visual_model) 也允许 position 为空, 前端
-- 布局算法兜底 (PackedBoxLayout 或网格布局)。手动提交体块的 user 必填 position,
-- 在 Pydantic schema 层校验 (BlockModelRequest.position_x/y 虽然声明为 Optional
-- 但 API 层会提示用户填)。
--
-- 为什么 numeric(10,2): 园区尺度一般 < 1km (100000m), 整数部分 5 位够用, 精度
-- 到 0.01m (1cm) 远超 CesiumJS 渲染精度需求。跟 length_m / width_m / height_m
-- 类型一致。
--
-- 不加索引: scene API 按 building_id + is_active 过滤 (已有
-- idx_visual_model_building_active 索引覆盖), position 不参与 SQL 查询条件,
-- 建索引浪费空间。

ALTER TABLE core.building_visual_model
    ADD COLUMN IF NOT EXISTS position_x numeric(10,2),
    ADD COLUMN IF NOT EXISTS position_y numeric(10,2);
