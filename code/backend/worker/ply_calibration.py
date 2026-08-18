"""
3DGS .ply 尺度校准 (Step 12)。

TripoSplat 输出的 .ply 已归一化到约 [-0.5, 0.5] 立方体 (xyz bbox 大约
0.99 x 0.73 x 0.94, 不是严格的 1x1x1)。客户给的建筑真实尺寸 (length/width/
height 米) 跟这个归一化尺寸不一致, 需要 plyfile 后处理把 splat 缩放到
真实尺寸。

数学 (协方差严格变换):

  3DGS 每个高斯点存:
    means   (x, y, z)         -- 中心坐标
    scales  (scale_0/1/2)     -- log 空间的高斯方差半径 (实际方差 = exp(scale))
    rotats  (rot_0/1/2/3)     -- 四元数旋转
    colors  (f_dc_0/1/2)      -- SH 0 阶系数 (含颜色)
    opacity (opacity)         -- logit 空间 (实际 alpha = sigmoid(opacity))
    normals (nx, ny, nz)      -- 法向量, 不影响渲染但 .ply 标准要求带

  协方差矩阵: Σ = R · diag(s²) · R^T
    其中 R 是从四元数恢复的 3x3 旋转矩阵, s = exp(scale_log) 是真实 scale。

  世界缩放 D = diag(sx, sy, sz) 后 (三轴独立缩放, 这是建筑 length/width/
  height 真实尺寸的要求):
    新中心:    m' = (m - center) · D
    新协方差:  Σ' = D · Σ · D^T

  这一步是关键: D · Σ · D^T 不等于 (D · R · S) · (D · R · S)^T, 因为
  D · R ≠ R · D (D 对角, R 不对角)。直接给 log(scale) 加 log(sx/sy/sz)
  是 (D · R · S) · (D · R · S)^T 的近似, 假设 R 接近单位矩阵 -- 对建筑
  重建不成立 (墙面/屋脊/山墙高斯 R 旋转很大, 45°/90° 常见), 会让高斯
  椭圆按错位轴缩放, 视觉上表现为"垂直拉长色块"。

  协方差严格变换流程:
    1. 从四元数恢复 R
    2. s = exp(scale_log)
    3. Σ = R · diag(s²) · R^T   (3x3 对称正定)
    4. Σ' = D · Σ · D^T         (仍是 3x3 对称正定, 因 D 可逆)
    5. eigh 分解: Σ' = U · Λ · U^T, U 正交, Λ 对角非负
    6. 新 s' = sqrt(Λ_ii), 新 R' = U
    7. 新 scale_log = log(s'), 新 quat = quat(R')

  数值要点:
    - eigh 数值误差可能让特征值出负 (实测 -1e-16 量级), clamp 到 1e-12
    - U 行列式可能 -1 (镜像反射), 翻转最后一列符号校正到右手系, 否则
      Cesium 渲染会镜像翻转
    - 大批量 (131072 个高斯) numpy 向量化: einsum 算 Σ, eigh 支持 stacked

  四元数 ↔ 矩阵转换的 batched numpy 实现沿用 triposplat.py 的 _quat_to_matrix
  / _matrix_to_quat (见 third_party/triposplat/triposplat.py), 这里复制一份
  避免对 triposplat 模块的硬依赖 (ply_calibration 是 utility, 不应绑死 worker
  的 sys.path 配置)。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from loguru import logger
from plyfile import PlyData, PlyElement


def _quat_to_matrix(q: np.ndarray) -> np.ndarray:
    """四元数 (w, x, y, z) batched 转旋转矩阵。

    输入 shape (N, 4), 输出 shape (N, 3, 3)。先归一化四元数防数值漂移。
    """
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.stack([
        1 - 2*(y*y + z*z), 2*(x*y - w*z),     2*(x*z + w*y),
        2*(x*y + w*z),     1 - 2*(x*x + z*z), 2*(y*z - w*x),
        2*(x*z - w*y),     2*(y*z + w*x),     1 - 2*(x*x + y*y),
    ], axis=-1).reshape(-1, 3, 3)
    return R


def _matrix_to_quat(R: np.ndarray) -> np.ndarray:
    """旋转矩阵 batched 转四元数 (w, x, y, z)。

    输入 shape (N, 3, 3), 输出 shape (N, 4)。按 trace 最大轴分三支处理
    数值稳定性 (Shepperd 分支法), 跟 triposplat.py 的实现一致。

    branch 选择:
      - trace + 1 > 0: 主分支, 用 s = sqrt(trace+1)*2 算 w
      - R[0,0] 最大: m01 分支, 用 s1 = sqrt(1+R00-R11-R22)*2 算 x
      - R[1,1] 最大: m11 分支, 用 s2 算 y
      - R[2,2] 最大: m21 分支, 用 s3 算 z
    每个分支单独算, 主分支算完用 mask 覆盖其他分支的点。
    """
    trace = R[:, 0, 0] + R[:, 1, 1] + R[:, 2, 2]
    q = np.zeros((R.shape[0], 4), dtype=R.dtype)
    s = np.sqrt(np.maximum(trace + 1, 0)) * 2
    q[:, 0] = 0.25 * s
    q[:, 1] = (R[:, 2, 1] - R[:, 1, 2]) / np.where(s != 0, s, 1)
    q[:, 2] = (R[:, 0, 2] - R[:, 2, 0]) / np.where(s != 0, s, 1)
    q[:, 3] = (R[:, 1, 0] - R[:, 0, 1]) / np.where(s != 0, s, 1)

    # 主分支 s=0 (trace <= -1, 180° 翻转) 时落到 m01/m11/m21 三个分支
    m01 = (R[:, 0, 0] >= R[:, 1, 1]) & (R[:, 0, 0] >= R[:, 2, 2]) & (s == 0)
    s1 = np.sqrt(np.maximum(1 + R[:, 0, 0] - R[:, 1, 1] - R[:, 2, 2], 0)) * 2
    q[m01, 0] = (R[m01, 2, 1] - R[m01, 1, 2]) / s1[m01]
    q[m01, 1] = 0.25 * s1[m01]
    q[m01, 2] = (R[m01, 0, 1] + R[m01, 1, 0]) / s1[m01]
    q[m01, 3] = (R[m01, 0, 2] + R[m01, 2, 0]) / s1[m01]

    m11 = (R[:, 1, 1] > R[:, 0, 0]) & (R[:, 1, 1] >= R[:, 2, 2]) & (s == 0)
    s2 = np.sqrt(np.maximum(1 + R[:, 1, 1] - R[:, 0, 0] - R[:, 2, 2], 0)) * 2
    q[m11, 0] = (R[m11, 0, 2] - R[m11, 2, 0]) / s2[m11]
    q[m11, 1] = (R[m11, 0, 1] + R[m11, 1, 0]) / s2[m11]
    q[m11, 2] = 0.25 * s2[m11]
    q[m11, 3] = (R[m11, 1, 2] + R[m11, 2, 1]) / s2[m11]

    m21 = (R[:, 2, 2] > R[:, 0, 0]) & (R[:, 2, 2] > R[:, 1, 1]) & (s == 0)
    s3 = np.sqrt(np.maximum(1 + R[:, 2, 2] - R[:, 0, 0] - R[:, 1, 1], 0)) * 2
    q[m21, 0] = (R[m21, 1, 0] - R[m21, 0, 1]) / s3[m21]
    q[m21, 1] = (R[m21, 0, 2] + R[m21, 2, 0]) / s3[m21]
    q[m21, 2] = (R[m21, 1, 2] + R[m21, 2, 1]) / s3[m21]
    q[m21, 3] = 0.25 * s3[m21]

    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def _build_covariance(R: np.ndarray, s: np.ndarray) -> np.ndarray:
    """批量算 3DGS 协方差 Σ = R · diag(s²) · R^T。

    输入:
      R: (N, 3, 3) 旋转矩阵
      s: (N, 3) 真实 scale (已 exp)
    输出:
      Σ: (N, 3, 3) 对称正定

    用 einsum 一步算:
      Σ[i, j] = sum_k R[i, k] * s[k]² * R[j, k]
      即 Σ = R @ diag(s²) @ R^T
    """
    return np.einsum('nki,nk,nkj->nij', R, s * s, R)


def _decompose_covariance(Sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """批量 eigh 分解协方差, 返回 (scale, R)。

    输入: Σ (N, 3, 3) 对称正定
    输出:
      s: (N, 3) sqrt(特征值), 即新 scale
      R: (N, 3, 3) 特征向量矩阵, 即新旋转

    数值处理:
      - 特征值 clamp 到 1e-12 (eigh 可能出 -1e-16 量级负值)
      - 行列式校正到 +1 (eigh 的 U 可能 det=-1, 是镜像反射不是旋转)
    """
    eigvals, eigvecs = np.linalg.eigh(Sigma)
    eigvals = np.maximum(eigvals, 1e-12)

    # det(U) < 0 时翻转最后一列符号校正到右手系
    dets = np.linalg.det(eigvecs)
    flip_mask = dets < 0
    if flip_mask.any():
        eigvecs[flip_mask, :, -1] *= -1

    s_new = np.sqrt(eigvals)
    return s_new, eigvecs


def calibrate_ply_scale(
    input_ply: Path,
    output_ply: Path,
    length_m: float,
    width_m: float,
    height_m: float,
) -> dict:
    """按 length/width/height 独立缩放三轴, 输出新 .ply。

    用协方差严格变换 (D · Σ · D^T 分解), 不是简单的 log(scale) += log(s_i)。
    建筑重建的高斯旋转较大 (墙面/屋脊/山墙), 简单加法会让高斯椭圆按错位
    轴缩放, 视觉上表现为"垂直拉长色块"。协方差变换严格保持高斯形状在
    世界坐标系下的几何意义。

    轴映射 (.ply 在 camera 坐标系 X right, Y down, Z forward):
      camera X (right)   -> length   (画面水平方向 = 建筑 length)
      camera Y (down)    -> height   (画面垂直方向 = 建筑 height, 向下)
      camera Z (forward) -> width    (画面深度方向 = 建筑 width)

    返回 {"sx": ..., "sy": ..., "sz": ..., "method": "covariance_eigh",
          "bbox_before": {...}, "bbox_after": {...}} 用于日志和验收。

    防御:
      - input_ply 不存在 -> FileNotFoundError (worker 主循环捕获走重试)
      - bbox 为 0 (退化模型) -> ValueError
    """
    if not input_ply.exists():
        raise FileNotFoundError(f"输入 .ply 不存在: {input_ply}")

    ply = PlyData.read(str(input_ply))
    v = ply["vertex"].data

    # 提取 means / scale_log / quat (注意 .ply 字段顺序: rot_0=w, rot_1=x, rot_2=y, rot_3=z)
    means = np.stack([v["x"], v["y"], v["z"]], axis=-1).astype(np.float64)
    scale_log = np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=-1).astype(np.float64)
    quat = np.stack([v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=-1).astype(np.float64)

    x_min, x_max = float(means[:, 0].min()), float(means[:, 0].max())
    y_min, y_max = float(means[:, 1].min()), float(means[:, 1].max())
    z_min, z_max = float(means[:, 2].min()), float(means[:, 2].max())
    bbox_x = x_max - x_min
    bbox_y = y_max - y_min
    bbox_z = z_max - z_min
    if bbox_x < 1e-6 or bbox_y < 1e-6 or bbox_z < 1e-6:
        raise ValueError(
            f"输入 .ply bbox 退化: x={bbox_x}, y={bbox_y}, z={bbox_z}, 无法校准"
        )

    sx = length_m / bbox_x
    sy = height_m / bbox_y
    sz = width_m / bbox_z
    D = np.array([sx, sy, sz], dtype=np.float64)
    cx = (x_min + x_max) / 2
    cy = (y_min + y_max) / 2
    cz = (z_min + z_max) / 2

    # 1. 中心化 means + 三轴独立缩放
    means_new = (means - np.array([cx, cy, cz])) * D[None, :]

    # 2. 协方差严格变换: Σ' = D · Σ · D^T, 然后 eigh 分解出新 scale 和 R
    R = _quat_to_matrix(quat)
    s = np.exp(scale_log)
    Sigma = _build_covariance(R, s)
    # D · Σ · D^T = Σ * (D_i * D_j) broadcast
    D_outer = D[:, None] * D[None, :]
    Sigma_new = Sigma * D_outer[None, :, :]
    s_new, R_new = _decompose_covariance(Sigma_new)

    new_scale_log = np.log(s_new)
    new_quat = _matrix_to_quat(R_new)

    # 3. 写回 .ply (其他字段 f_dc_*/opacity/nx,ny,nz 原样复制)
    new_v = np.zeros(v.shape, dtype=v.dtype)
    field_map = {
        "x": means_new[:, 0],
        "y": means_new[:, 1],
        "z": means_new[:, 2],
        "scale_0": new_scale_log[:, 0],
        "scale_1": new_scale_log[:, 1],
        "scale_2": new_scale_log[:, 2],
        "rot_0": new_quat[:, 0],
        "rot_1": new_quat[:, 1],
        "rot_2": new_quat[:, 2],
        "rot_3": new_quat[:, 3],
    }
    for name in v.dtype.names:
        if name in field_map:
            new_v[name] = field_map[name]
        else:
            new_v[name] = v[name]

    output_ply.parent.mkdir(parents=True, exist_ok=True)
    new_ply = PlyData([PlyElement.describe(new_v, "vertex")], text=False)
    new_ply.write(str(output_ply))

    bbox_after = {
        "x": (float(new_v["x"].min()), float(new_v["x"].max())),
        "y": (float(new_v["y"].min()), float(new_v["y"].max())),
        "z": (float(new_v["z"].min()), float(new_v["z"].max())),
    }
    logger.info(
        "calibrate_ply_scale (covariance_eigh): sx={:.3f} sy={:.3f} sz={:.3f} "
        "(target L={} W={} H={}m, axis map: camX->L camY->H camZ->W, "
        "N={}, scale_max raw={:.4f} -> new={:.4f})",
        sx, sy, sz, length_m, width_m, height_m,
        len(v), float(np.exp(scale_log).max()), float(s_new.max()),
    )
    return {
        "sx": sx,
        "sy": sy,
        "sz": sz,
        "method": "covariance_eigh",
        "bbox_before": {"x": (x_min, x_max), "y": (y_min, y_max), "z": (z_min, z_max)},
        "bbox_after": bbox_after,
        "scale_max_before": float(np.exp(scale_log).max()),
        "scale_max_after": float(s_new.max()),
    }
