"""
3DGS preview 渲染 (Step 12)。

从 .ply 渲染一张 500x500 PNG 给前端在 job 列表里展示用。

思路: 投影 3D 高斯到 2D 屏幕 -> matplotlib scatter 画散点。

不是真正的 splat rendering:
  - 不计算 2D 屏幕空间高斯协方差 (3D 协方差投影后的精确 2D 形状)
  - 不做严格的 alpha compositing (每个像素遍历所有覆盖它的高斯做累加)

近似处理:
  - 每个高斯在屏幕上画一个圆, 半径 = scale 均值 * fx / z (像素)
  - alpha = opacity (sigmoid 后), 大小 = pi * (2*radius)^2
  - matplotlib scatter 用 alpha blending 模拟 splat 效果
  - 按 z 排序 (远到近) 保证遮挡正确

效果: 视觉上接近 splat (粒子云感), 速度比真正的 splat 渲染快
(numpy + matplotlib 1.14s vs gsplat GPU 5s+)。代价是细节稍差
(高斯椭圆被画成圆, 各向异性被忽略)。一期 demo 够用, 后续如果要更
高质量的 preview 可以考虑装 VS Build Tools 让 gsplat 跑。

不依赖 CUDA: 全程 CPU + numpy + matplotlib, 任何机器都能跑。

坐标系约定 (.ply 在 camera 坐标系):
  X right (画面水平向右)
  Y down  (画面垂直向下, 跟 OpenGL 相反)
  Z forward (远离相机, 朝画面里)

所以"上"是 -Y 方向, "右"是 +X 方向, "前"是 +Z 方向。相机要摆在
模型的 -Z 后方 (朝 +Z 看) 才能看到模型正面。早期版本 up=(0,0,1)
是错的, 那把 Z 当上方向, 实际 .ply 的 Z 是 forward。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from loguru import logger
from PIL import Image, ImageDraw
from plyfile import PlyData


def _load_ply(ply_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """读 .ply 返回 means/scales/colors/opacities (numpy 数组)。

    3DGS 标准 .ply 字段:
      means:    x, y, z
      scales:   scale_0, scale_1, scale_2 (log 空间, exp 后是真实 scale)
      colors:   f_dc_0, f_dc_1, f_dc_2 (SH 0 阶系数, RGB = 0.5 + 0.28 * f_dc)
      opacities: opacity (logit, sigmoid 后是 alpha)
    """
    ply = PlyData.read(str(ply_path))
    v = ply["vertex"].data

    means = np.stack([v["x"], v["y"], v["z"]], axis=-1).astype(np.float32)
    scales = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=-1).astype(np.float32))
    f_dc = np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=-1).astype(np.float32)
    colors = np.clip(0.5 + 0.28209479 * f_dc, 0.0, 1.0)
    opacities = 1.0 / (1.0 + np.exp(-v["opacity"].astype(np.float32)))
    return means, scales, colors, opacities


def _make_viewmat(xyz_bbox: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """根据 bbox 摆一个相机, 从右前上方斜视模型。

    相机位置: 在 bbox 中心斜右前上方, 距离 = bbox 对角线 * 1.2。
      相机偏移 = (distance*0.5, -distance*0.4, -distance*0.8)
        +X 右侧 (相机在右, 看左侧模型)
        -Y 上方 (camera Y down, 所以 -Y 是上)
        -Z 后方 (朝 +Z 看, 即朝模型正面方向看)
    distance 用 bbox 对角线 (三轴 norm) 不是 max extent, 因为 max extent
    在细长模型 (如 105x14x70) 下低估了实际尺寸, 让相机太近导致 perspective
    distortion 严重。对角线综合考虑三轴, 视觉舒适。

    up 向量: (0, -1, 0) -- camera Y down, 所以 -Y 是画面"上方"。
    早期版本 up=(0,0,1) 错把 Z 当上方向 (.ply 的 Z 是 forward), 视图矩阵
    算出来的 right 跟实际画面方向反了, 高斯被投影到画面外。

    返回 (viewmat, eye, target)。viewmat 是 4x4 世界 -> 相机变换矩阵,
    坐标系约定: 相机看 +z 方向 (不是 OpenGL 默认的 -z), means_cam.z > 0
    表示在相机前方。
    """
    center = (xyz_bbox[:, 0] + xyz_bbox[:, 1]) / 2
    extent = xyz_bbox[:, 1] - xyz_bbox[:, 0]
    # bbox 对角线长度, 综合考虑三轴
    diagonal = float(np.linalg.norm(extent))
    distance = diagonal * 1.2
    eye = center + np.array(
        [distance * 0.5, -distance * 0.4, -distance * 0.8], dtype=np.float32
    )
    target = center.copy()
    # camera Y down, 画面"上"对应世界 -Y 方向
    up = np.array([0.0, -1.0, 0.0], dtype=np.float32)

    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    new_up = np.cross(right, forward)

    # 相机 +z 轴 = forward (看 +z 方向, 跟 OpenGL -z 相反)
    R = np.stack([right, new_up, forward], axis=0)
    T = -R @ eye
    viewmat = np.eye(4, dtype=np.float32)
    viewmat[:3, :3] = R
    viewmat[:3, 3] = T
    return viewmat, eye, target


def _compute_adaptive_focal(
    xyz_bbox: np.ndarray, viewmat: np.ndarray, image_size: int, target_ratio: float = 0.7
) -> float:
    """根据 model 8 角点投影范围动态算 fx, 让 model 占画面 target_ratio (默认 70%)。

    早期版本 fx = image_size * 1.5 是固定值, 对 calibrate 后的大模型 (105m) 太
    长焦, model 只占画面 10%, 大部分像素是白色背景 -> preview "几乎全白"。

    流程:
      1. 用 fx_init=image_size 算 8 角点投影范围
      2. fx = target_ratio * image_size / max(x_range, y_range) * fx_init
      3. 这样 model 投影占画面 target_ratio

    用 max(x_range, y_range) 而不是分别用 x/y range 算 fx/fy, 是因为各向
    同性 fx=fy 才不会让 model 在画面里被拉伸 (建筑重建的 splat 已经够变形了,
    preview 别再加分)。
    """
    x_min, x_max = float(xyz_bbox[0, 0]), float(xyz_bbox[0, 1])
    y_min, y_max = float(xyz_bbox[1, 0]), float(xyz_bbox[1, 1])
    z_min, z_max = float(xyz_bbox[2, 0]), float(xyz_bbox[2, 1])
    corners = np.array([
        [x_min, y_min, z_min], [x_min, y_min, z_max],
        [x_min, y_max, z_min], [x_min, y_max, z_max],
        [x_max, y_min, z_min], [x_max, y_min, z_max],
        [x_max, y_max, z_min], [x_max, y_max, z_max],
    ], dtype=np.float32)
    corners_h = np.concatenate([corners, np.ones((8, 1), dtype=np.float32)], axis=1)
    corners_cam = (viewmat @ corners_h.T).T
    z_cam = np.maximum(corners_cam[:, 2], 1e-6)

    fx_init = float(image_size)
    x_proj = fx_init * corners_cam[:, 0] / z_cam
    y_proj = fx_init * corners_cam[:, 1] / z_cam
    x_range = float(x_proj.max() - x_proj.min())
    y_range = float(y_proj.max() - y_proj.min())
    if x_range < 1e-6 or y_range < 1e-6:
        return fx_init

    return target_ratio * float(image_size) / max(x_range, y_range) * fx_init


def _project(
    means: np.ndarray, viewmat: np.ndarray, K: np.ndarray, W: int, H: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """3D 点投影到 2D 屏幕。返回 (x, y, z)。z 是相机系深度, >0 表示在相机前方。"""
    means_h = np.concatenate(
        [means, np.ones((means.shape[0], 1), dtype=np.float32)], axis=1
    )
    means_cam = (viewmat @ means_h.T).T
    z = np.maximum(means_cam[:, 2], 1e-6)
    x_proj = K[0, 0] * means_cam[:, 0] / z + K[0, 2]
    y_proj = K[1, 1] * means_cam[:, 1] / z + K[1, 2]
    return x_proj, y_proj, z


def render_preview(ply_path: Path, output_png: Path, image_size: int = 500) -> dict:
    """从 .ply 渲染 preview.png。

    返回 {"render_time_sec": ..., "visible_gaussians": ..., "image_size": ...}。

    防御:
      - .ply 不存在 -> FileNotFoundError (worker 主循环捕获走重试)
      - .ply 没有 vertex 元素 -> KeyError -> 上抛走重试
    """
    if not ply_path.exists():
        raise FileNotFoundError(f"输入 .ply 不存在: {ply_path}")

    t0 = time.time()
    means, scales, colors, opacities = _load_ply(ply_path)

    xyz_bbox = np.stack([means.min(axis=0), means.max(axis=0)], axis=-1)
    viewmat, _, _ = _make_viewmat(xyz_bbox)

    W = H = image_size
    # 自适应焦距: 8 角点投影后让 model 占画面 90%, 而不是固定 fx=H*1.0。
    # 固定 fx 对 calibrate 后大模型 (105m) 太长焦, model 只占画面 10%, 大部分
    # 像素是白色背景 -> preview "几乎全白"。target_ratio=0.9 让 model 占画面
    # 83% (实测 100% 高斯仍在画面内, 8 角点中长边占 90%), 既看清建筑又留少量
    # 边距。0.95 / 1.0 收益递减 (non_white +2%), 不值得边缘风险。
    fx = fy = _compute_adaptive_focal(xyz_bbox, viewmat, image_size, target_ratio=0.9)
    K = np.array(
        [[fx, 0, W / 2], [0, fy, H / 2], [0, 0, 1]], dtype=np.float32
    )

    x, y, z = _project(means, viewmat, K, W, H)
    # 屏幕半径 (像素) = 高斯尺度均值 * fx / 深度
    # 用三轴 scale 均值近似 (不考虑各向异性), 后续要更精确可以用 max
    scale_avg = scales.mean(axis=1)
    radius_px = scale_avg * fx / np.abs(z)

    # 边界放宽到 100 像素 (旧值 50 太严, 边缘高斯被裁掉导致 preview 偏空)
    visible = (
        (x > -100) & (x < W + 100)
        & (y > -100) & (y < H + 100)
        & (z > 0)
        & (opacities > 0.05)
    )
    x, y, z = x[visible], y[visible], z[visible]
    colors, opacities, radius_px = (
        colors[visible], opacities[visible], radius_px[visible]
    )

    if len(x) == 0:
        raise ValueError(f".ply 渲染后无可见高斯点, 模型可能异常: {ply_path}")

    # 远到近排序, 让近的高斯盖住远的
    idx = np.argsort(-z)
    x, y, colors, opacities, radius_px = (
        x[idx], y[idx], colors[idx], opacities[idx], radius_px[idx]
    )

    # Pillow ImageDraw 渲染 (2026-07-29 退回方案):
    # 之前用 matplotlib scatter, 但 matplotlib 的 C 扩展 _path.pyd 被 Windows
    # Smart App Control (Enforce 模式) 拦截, worker 启动 import 失败。退回
    # Pillow, 不依赖 matplotlib。
    #
    # Pillow ImageDraw.ellipse 逐个画圆, 实测 13 万个圆约 0.5 秒 (radius 多为
    # 1-3 像素, 单圆开销小), 不需要采样。不透明画圆 (不做 alpha blending),
    # 按 z 远到近排序让近的覆盖远的, 视觉接近 splat 遮挡效果, 建筑轮廓比
    # matplotlib 半透明更清晰。非白像素覆盖率 ~20%, 跟 matplotlib 版 (20.4%)
    # 持平。
    # radius clip: <1 画不出, >15 盖住整个画面
    radius_px = np.clip(radius_px, 1.0, 15.0)

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    for i in range(len(x)):
        xi = float(x[i])
        yi = float(y[i])
        r = float(radius_px[i])
        c = colors[i]
        rgb = (int(c[0] * 255), int(c[1] * 255), int(c[2] * 255))
        draw.ellipse((xi - r, yi - r, xi + r, yi + r), fill=rgb)

    output_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_png))

    render_time = time.time() - t0
    logger.info(
        "render_preview: gaussians={} (visible={}) time={:.2f}s -> {}",
        len(means), len(x), render_time, output_png,
    )
    return {
        "render_time_sec": render_time,
        "visible_gaussians": int(len(x)),
        "image_size": image_size,
    }
