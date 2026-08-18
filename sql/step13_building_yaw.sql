-- Step 13: 给 core.building_visual_model 加 yaw_deg 列 (水平旋转角度)
--
-- 存什么: 建筑水平旋转角度 (度, 0-360)。前端 BuildingSplat 渲染时把 yaw_deg
-- 转成弧度, 在 ENU 平移和 tile 局部缩放之间插入 R_z 旋转矩阵, 让 splat 绕
-- "上"方向 (ENU Z 轴) 旋转。BuildingBlock 灰盒不旋转 (axis-aligned 简化)。
--
-- 为什么加到 building_visual_model 而不是 building:
--   yaw 跟 position_x/y 一样都是"渲染摆放参数", 由用户在 BuildingDetailDrawer
--   里调整, 跟模型本身 (BLOCK / PHOTO_SINGLE) 绑定。同 building 切换模型时
--   yaw 不一定能沿用 (BLOCK 模式不旋转, splat 模式才需要), 所以放 visual_model
--   而不是 building。
--
-- 为什么 NUMERIC(5,2) DEFAULT 0 NOT NULL:
--   NUMERIC(5,2) 支持 -999.99 到 999.99, yaw 0-360 足够 (1° 步长滑块, 0.01
--   精度足够前端显示)。DEFAULT 0 = 不旋转, 老记录自动有值不用回填。NOT NULL
--   避免 NULL 处理 (前端拿到 None 还得 fallback 到 0, 多此一举)。
--
-- 为什么单独加列而不是用 position_x/y 算朝向:
--   朝向跟位置是独立的渲染参数, 不应该耦合。位置是建筑在园区的 footprint
--   中心, 朝向是建筑绕中心的旋转角度。两者独立调整更直观。

ALTER TABLE core.building_visual_model
    ADD COLUMN IF NOT EXISTS yaw_deg NUMERIC(5,2) DEFAULT 0 NOT NULL;
