"""临时脚本: 验证 numpy + matplotlib 能不能渲染 3DGS preview.png。跑完删。

思路: 把 3D 高斯投影到 2D 屏幕坐标, 用 matplotlib scatter 画散点,
     每个点大小 = 高斯 scale (像素), 颜色 = SH C0 转 RGB, alpha = opacity。
     按 z 排序 (远到近) 保证遮挡正确。

这不算真正的 splat rendering (不计算 2D 协方差, 不做 alpha compositing),
     但作为 preview 够用, 速度比 gsplat 快, 不需要 CUDA toolkit。

依赖 _test_triposplat_inference.py 生成的 output.ply。

跑法:
    cd backend
    python _test_numpy_preview.py
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import numpy as np
import torch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from plyfile import PlyData

BACKEND_ROOT = Path(__file__).resolve().parent
PLY_PATH = BACKEND_ROOT / "storage" / "tripoosplat" / "test_out" / "output.ply"
OUT_PNG = BACKEND_ROOT / "storage" / "tripoosplat" / "test_out" / "preview_numpy.png"


def load_ply(ply_path):
    """读 .ply 返回 means, scales, colors, opacities (numpy 数组)。"""
    ply = PlyData.read(str(ply_path))
    v = ply["vertex"].data

    means = np.stack([v["x"], v["y"], v["z"]], axis=-1).astype(np.float32)
    scales = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=-1).astype(np.float32))
    f_dc = np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=-1).astype(np.float32)
    colors = np.clip(0.5 + 0.28209479 * f_dc, 0.0, 1.0)
    opacities = 1.0 / (1.0 + np.exp(-v["opacity"].astype(np.float32)))
    return means, scales, colors, opacities


def make_viewmat(xyz_bbox):
    """根据 bbox 中心摆一个相机 (看向中心, 俯视角度)。"""
    center = (xyz_bbox[:, 0] + xyz_bbox[:, 1]) / 2
    extent = (xyz_bbox[:, 1] - xyz_bbox[:, 0]).max()
    distance = extent * 2.5
    eye = center + np.array([distance * 0.5, -distance * 0.6, distance * 0.5], dtype=np.float32)
    target = center.copy()
    up = np.array([0.0, 0.0, 1.0], dtype=np.float32)

    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    new_up = np.cross(right, forward)

    R = np.stack([right, new_up, forward], axis=0)
    T = -R @ eye

    viewmat = np.eye(4, dtype=np.float32)
    viewmat[:3, :3] = R
    viewmat[:3, 3] = T
    return viewmat


def project(means, viewmat, K, W, H):
    """3D 点投影到 2D 屏幕。返回 x, y, depth。"""
    means_h = np.concatenate([means, np.ones((means.shape[0], 1), dtype=np.float32)], axis=1)
    means_cam = (viewmat @ means_h.T).T
    z = means_cam[:, 2]
    # 防止 z=0
    z = np.maximum(z, 1e-6)
    x_proj = K[0, 0] * means_cam[:, 0] / z + K[0, 2]
    y_proj = K[1, 1] * means_cam[:, 1] / z + K[1, 2]
    return x_proj, y_proj, z


def main():
    print(f"输入 .ply: {PLY_PATH}")
    print(f"输出 PNG: {OUT_PNG}")
    print()

    print("=== 1. 加载 .ply ===")
    t0 = time.time()
    means, scales, colors, opacities = load_ply(PLY_PATH)
    print(f"加载耗时: {time.time() - t0:.2f}s")
    print(f"高斯点数: {means.shape[0]}")
    print()

    print("=== 2. 投影到 2D ===")
    xyz_bbox = np.stack([
        means.min(axis=0),
        means.max(axis=0),
    ], axis=-1)
    viewmat = make_viewmat(xyz_bbox)

    W, H = 512, 512
    fx = fy = H * 1.5
    K = np.array([
        [fx, 0, W / 2],
        [0, fy, H / 2],
        [0, 0, 1],
    ], dtype=np.float32)

    t0 = time.time()
    x, y, z = project(means, viewmat, K, W, H)
    print(f"投影耗时: {time.time() - t0:.2f}s")

    # 把点大小映射成屏幕像素大小: scale * fx / z
    # 用三个 axis scale 的均值 (各向同性近似)
    scale_avg = scales.mean(axis=1)
    radius_px = scale_avg * fx / np.abs(z)
    print(f"radius_px range: {radius_px.min():.2f} ~ {radius_px.max():.2f}")
    print(f"radius_px median: {np.median(radius_px):.2f}")
    print()

    print("=== 3. 过滤屏幕外的点 ===")
    visible = (x > -50) & (x < W + 50) & (y > -50) & (y < H + 50) & (z > 0) & (radius_px > 0.5) & (opacities > 0.05)
    print(f"可见高斯: {visible.sum()} / {len(visible)}")
    print()

    print("=== 4. 按 z 排序 (远到近) ===")
    x, y, z = x[visible], y[visible], z[visible]
    colors, opacities, radius_px = colors[visible], opacities[visible], radius_px[visible]
    idx = np.argsort(-z)  # 远的先画
    x, y, colors, opacities, radius_px = x[idx], y[idx], colors[idx], opacities[idx], radius_px[idx]
    print()

    print("=== 5. matplotlib scatter 渲染 ===")
    t0 = time.time()
    fig, ax = plt.subplots(figsize=(5, 5), dpi=100)
    # scatter: s 参数是面积 (pixel^2), 所以 s = pi * r^2
    # 但 matplotlib 默认 dpi 不同, 这里直接用点 size
    sizes = np.pi * (radius_px * 2) ** 2  # 用 2*r 当半径
    sizes = np.clip(sizes, 1, 500)
    ax.scatter(
        x, y,
        s=sizes,
        c=colors,
        alpha=opacities * 0.8,
        marker="o",
        edgecolors="none",
    )
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)  # y 轴翻转 (图像坐标)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.invert_yaxis()
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    plt.savefig(str(OUT_PNG), dpi=100, bbox_inches="tight", pad_inches=0)
    plt.close()
    print(f"渲染耗时: {time.time() - t0:.2f}s")
    print(f"已保存: {OUT_PNG}")
    print(f"  大小: {OUT_PNG.stat().st_size / 1024:.1f} KB")

    print()
    print("=== ALL DONE ===")


if __name__ == "__main__":
    main()
