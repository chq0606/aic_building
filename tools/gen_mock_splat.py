"""
生成 mock splat .ply 文件 (Step 14)
=============================================================================
作用:
  前端 BuildingSplat.vue 用这个 .ply 文件做 splat 渲染验证。
  实际项目中 splat 是 TripoSplat 生成的真实建筑点云, 但 demo 数据库
  没有真实 .ply 模型, 这里生成一个 100 高斯点的"假楼"作为测试桩。

生成内容:
  - 一个长方体轮廓 (30m x 20m x 12m, 模拟一栋楼)
  - 表面均匀分布 100 个高斯点
  - 每点带颜色 (按高度从下到上琥珀 -> 暖灰渐变)
  - 高斯半径 0.3m, 不透明度 0.85
  - 旋转四元数 = 单位四元数 (无旋转)

输出格式 (3DGS .ply 标准):
  ply
  format binary_little_endian 1.0
  element vertex 100
  property float x
  property float y
  property float z
  property float nx
  property float ny
  property float nz
  property float f_dc_0
  property float f_dc_1
  property float f_dc_2
  property float opacity
  property float scale_0
  property float scale_1
  property float scale_2
  property float rot_0
  property float rot_1
  property float rot_2
  property float rot_3
  end_header
  <binary data>

  其中:
    - x/y/z: 米单位 (相对楼中心, 中心在 0,0,0)
    - nx/ny/nz: 法向量 (统一 0,0,1)
    - f_dc_0/1/2: SH 0 阶系数 (Cesium 0阶 = 颜色/255 * 0.2821)
    - opacity: logit 空间 (sigmoid(opacity) = alpha)
    - scale_0/1/2: log 空间高斯方差半径
    - rot_0/1/2/3: 四元数 (单位 = 1,0,0,0)

用法:
  python tools/gen_mock_splat.py
  -> 输出 code/frontend/public/mock/mock_splat.ply

依赖:
  plyfile (pip install plyfile)
  numpy
"""

import math
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


# 楼尺寸 (跟 demo 数据 Bobcat_education_Tammy 一致)
BUILDING_LEN = 30.0   # X 方向长度
BUILDING_WID = 20.0   # Y 方向宽度
BUILDING_HGT = 12.0   # Z 方向高度

# 高斯点数 (表面均匀采样)
N_POINTS = 100

# SH 0 阶系数 = color / 255 * 0.2821
SH_C0 = 0.2821

# 顶部琥珀色, 底部暖灰色 (颜色按高度渐变)
COLOR_TOP = np.array([212, 155, 59])    # #D49B3B 琥珀
COLOR_BOT = np.array([74, 74, 74])      # #4A4A4A 暖灰


def gen_points(n: int) -> np.ndarray:
    """生成长方体表面的均匀采样点"""
    # 6 个面: top/bottom (X*Y), front/back (X*Z), left/right (Y*Z)
    # 各面面积比例: top/bottom=600, front/back=360, left/right=240
    # 总面积 2400, 按比例分配点数
    n_top = max(1, int(n * 600 / 2400))
    n_bot = n_top
    n_fb = max(1, int(n * 360 / 2400))
    n_lr = max(1, int(n * 240 / 2400))

    points = []

    # top 面 (z=HGT/2)
    for _ in range(n_top):
        x = np.random.uniform(-BUILDING_LEN/2, BUILDING_LEN/2)
        y = np.random.uniform(-BUILDING_WID/2, BUILDING_WID/2)
        points.append([x, y, BUILDING_HGT/2])
    # bottom 面 (z=-HGT/2)
    for _ in range(n_bot):
        x = np.random.uniform(-BUILDING_LEN/2, BUILDING_LEN/2)
        y = np.random.uniform(-BUILDING_WID/2, BUILDING_WID/2)
        points.append([x, y, -BUILDING_HGT/2])

    # front/back (y=±WID/2)
    for _ in range(n_fb * 2):
        x = np.random.uniform(-BUILDING_LEN/2, BUILDING_LEN/2)
        z = np.random.uniform(-BUILDING_HGT/2, BUILDING_HGT/2)
        side = 1 if np.random.random() > 0.5 else -1
        points.append([x, side * BUILDING_WID/2, z])

    # left/right (x=±LEN/2)
    for _ in range(n_lr * 2):
        y = np.random.uniform(-BUILDING_WID/2, BUILDING_WID/2)
        z = np.random.uniform(-BUILDING_HGT/2, BUILDING_HGT/2)
        side = 1 if np.random.random() > 0.5 else -1
        points.append([side * BUILDING_LEN/2, y, z])

    arr = np.array(points[:n], dtype=np.float32)
    return arr


def build_ply(points: np.ndarray) -> PlyData:
    """构造 .ply 数据 (3DGS 标准 17 字段)"""
    n = len(points)

    # 法向量: 统一 (0, 0, 1) 不影响渲染
    nx = np.zeros(n, dtype=np.float32)
    ny = np.zeros(n, dtype=np.float32)
    nz = np.ones(n, dtype=np.float32)

    # 颜色: 按 z 高度从底部暖灰渐变到顶部琥珀
    z_min = points[:, 2].min()
    z_max = points[:, 2].max()
    z_range = max(z_max - z_min, 1e-6)
    t = (points[:, 2] - z_min) / z_range  # 0~1
    colors = np.outer(1 - t, COLOR_BOT) + np.outer(t, COLOR_TOP)  # (n, 3)

    # SH 0 阶: f_dc = color / 255 * SH_C0
    f_dc_0 = (colors[:, 0] / 255.0 * SH_C0).astype(np.float32)
    f_dc_1 = (colors[:, 1] / 255.0 * SH_C0).astype(np.float32)
    f_dc_2 = (colors[:, 2] / 255.0 * SH_C0).astype(np.float32)

    # opacity: logit(0.85) ≈ 1.735 (sigmoid(1.735) ≈ 0.85)
    opacity = np.full(n, math.log(0.85 / (1 - 0.85)), dtype=np.float32)

    # scale: log(0.3) ≈ -1.204 (实际高斯半径 = exp(-1.204) ≈ 0.3m)
    log_scale = math.log(0.3)
    scale_0 = np.full(n, log_scale, dtype=np.float32)
    scale_1 = np.full(n, log_scale, dtype=np.float32)
    scale_2 = np.full(n, log_scale, dtype=np.float32)

    # 旋转: 单位四元数 (1, 0, 0, 0)
    rot_0 = np.ones(n, dtype=np.float32)
    rot_1 = np.zeros(n, dtype=np.float32)
    rot_2 = np.zeros(n, dtype=np.float32)
    rot_3 = np.zeros(n, dtype=np.float32)

    # 拼成结构化数组
    dtype = [
        ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
        ('nx', 'f4'), ('ny', 'f4'), ('nz', 'f4'),
        ('f_dc_0', 'f4'), ('f_dc_1', 'f4'), ('f_dc_2', 'f4'),
        ('opacity', 'f4'),
        ('scale_0', 'f4'), ('scale_1', 'f4'), ('scale_2', 'f4'),
        ('rot_0', 'f4'), ('rot_1', 'f4'), ('rot_2', 'f4'), ('rot_3', 'f4'),
    ]
    structured = np.zeros(n, dtype=dtype)
    structured['x'] = points[:, 0]
    structured['y'] = points[:, 1]
    structured['z'] = points[:, 2]
    structured['nx'] = nx
    structured['ny'] = ny
    structured['nz'] = nz
    structured['f_dc_0'] = f_dc_0
    structured['f_dc_1'] = f_dc_1
    structured['f_dc_2'] = f_dc_2
    structured['opacity'] = opacity
    structured['scale_0'] = scale_0
    structured['scale_1'] = scale_1
    structured['scale_2'] = scale_2
    structured['rot_0'] = rot_0
    structured['rot_1'] = rot_1
    structured['rot_2'] = rot_2
    structured['rot_3'] = rot_3

    el = PlyElement.describe(structured, 'vertex')
    return PlyData([el], text=False, byte_order='<')


def main():
    output = Path(__file__).parent.parent / 'code' / 'frontend' / 'public' / 'mock' / 'mock_splat.ply'
    output.parent.mkdir(parents=True, exist_ok=True)

    np.random.seed(42)  # 固定种子保证可复现
    points = gen_points(N_POINTS)
    ply = build_ply(points)
    ply.write(str(output))

    print(f'已生成 mock splat: {output}')
    print(f'  点数: {N_POINTS}')
    print(f'  楼尺寸: {BUILDING_LEN}m x {BUILDING_WID}m x {BUILDING_HGT}m')
    print(f'  文件大小: {output.stat().st_size} bytes')

    # 简单校验: 读回来打印前 3 个点
    verify = PlyData.read(str(output))
    v = verify['vertex'].data
    print(f'  验证: 第一个点 x={v[0]["x"]:.2f} y={v[0]["y"]:.2f} z={v[0]["z"]:.2f}')
    print(f'  颜色 (f_dc_0): {v[0]["f_dc_0"]:.4f} (= {v[0]["f_dc_0"]/SH_C0*255:.0f}/255)')


if __name__ == '__main__':
    main()
