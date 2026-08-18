-- Step 12 (后续补丁): 给 core.building_visual_model 加 tiles_path 列
--
-- 存什么: 3D Tiles tileset.json 的绝对路径。TripoSplat worker 生成 .ply 后
-- 调 3dgs-ply-3dtiles-converter 转 3D Tiles (Cesium 1.143+ 原生支持 KHR_gaussian_
-- splatting GLB tile), 输出目录 {job_dir}/tiles/, tileset.json 在该目录下。
--
-- 为什么单独加列而不是从 storage_path 推导:
--   1. storage_path 是 .ply 路径, 没规定 .ply 跟 tiles/ 一定同目录 (后续可能
--      把 tiles 放对象存储, .ply 留本地)
--   2. tiles 转换可能失败 (Node 没装 / .ply 损坏), 这种情况 tiles_path=NULL
--      但 storage_path 仍有值, 前端能 fallback 到 .ply 静态点云渲染
--   3. 跟 storage_path / preview_image_path 同级语义清晰: 三个字段各对应一种
--      产物 (原始 .ply / 预览图 / 3D Tiles), 业务上互不耦合
--
-- 为什么 nullable: 旧 PHOTO_SINGLE 记录 (Step 12 初版跑的) 没 tiles, 加列后
-- 默认 NULL, 前端拿到 NULL 时降级走 .ply 接口。BLOCK 模式没 tiles 也 NULL。
--
-- 为什么 text 不 numeric: 跟 storage_path / preview_image_path 类型一致, 都是
-- 文件系统绝对路径字符串。

ALTER TABLE core.building_visual_model
    ADD COLUMN IF NOT EXISTS tiles_path text;
