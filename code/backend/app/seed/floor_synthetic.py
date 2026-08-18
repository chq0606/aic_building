"""
楼层虚拟数据生成 seed。

BDG2 数据集只有楼栋级总表 (core.point point_kind='METER'), 没有楼层级数据。
这个模块按"面积×用途权重 + 楼栋时序 shape 复用 + 楼层特征噪声 + 逐小时比例
归一化"生成楼层级 SENSOR point 的逐小时读数, 满足"各楼层加总 = 楼栋总量"的
物理约束 (数学上严格成立, 误差仅浮点级 1e-15)。

调用方式:
    python -m app.seed.cli bdg2 --reset   # bdg2 seed 末尾自动接调本模块
    python -m app.seed.cli floor-synthetic --reset  # 独立跑

楼层数据 source_dataset='synthetic_floor', 真实导入楼层数据时按这个标记区分,
一键清理不影响真实楼栋总表:
    DELETE FROM core.floor WHERE source_dataset = 'synthetic_floor'

算法核心 (加总约束):
    floor_curve_raw[i][t] = building_curve[t] × w_norm[i] × floor_shape[i][t] × (1 + noise[i][t])
    scale[t] = building_curve[t] / Σ_i floor_curve_raw[i][t]
    floor_curve[i][t] = floor_curve_raw[i][t] × scale[t]

    归一化后 Σ_i floor_curve[i][t] = building_curve[t] 严格成立。

内存控制 (16GB RAM 限制):
    楼栋粒度循环, 每栋楼处理完立即 COPY + commit 释放 numpy 数组。一栋楼最多
    6 能源 × 6 层 × 8760 小时 ≈ 31 万行, numpy 数组约 2.5MB, 峰值内存可控。
"""
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from io import StringIO

import numpy as np
from loguru import logger

from app.db.session import get_conn


# ============================================================================
# 楼层用途分配表
# ----------------------------------------------------------------------------
# 每栋楼的楼层数 + 每层用途。Alissa 真实 2 层, 虚拟扩展到 5 层 (能源最丰富,
# demo 主推, 2 层演示效果弱)。Franklin 真实 1 层, 扩展到 3 层 (1 层太单薄)。
# 其他楼用真实 floors_count。
#
# floor_type 决定能源分布:
#   gas 只在 LAB/MECHANICAL/SPORTS 层 (实验室/机房/体育场馆才有燃气)
#   solar 只在 is_rooftop=true 的顶层 (屋顶光伏)
#
# 楼栋能源-楼层匹配复核:
#   Alissa  (gas✓ solar✓): 5F LAB rooftop -> gas + solar 都能落 ✓
#   Dylan   (gas✗ solar✓): 2F OFFICE rooftop -> solar 落, 无 gas 无所谓 ✓
#   Seth    (gas✓ solar✗): 4F MECHANICAL rooftop -> gas 落, 无 solar ✓
#   Franklin(gas✓ solar✗): 3F MECHANICAL rooftop -> gas 落 ✓
#   Tammy   (gas✗ solar✗): 6F MECHANICAL rooftop -> 都没, 无所谓 ✓
#   Angie   (gas✗ solar✗): 4F MECHANICAL rooftop -> 都没, 无所谓 ✓
# ============================================================================
FLOOR_PLAN = {
    "Bobcat_education_Alissa": [
        (1, "1F 大堂",       "LOBBY",         False),
        (2, "2F 教室",       "CLASSROOM",     False),
        (3, "3F 教室",       "CLASSROOM",     False),
        (4, "4F 办公",       "OFFICE",        False),
        (5, "5F 实验室",     "LAB",           True),
    ],
    "Bobcat_education_Dylan": [
        (1, "1F 学生中心",   "STUDENT_CENTER", False),
        (2, "2F 办公",       "OFFICE",         True),
    ],
    "Bobcat_education_Seth": [
        (1, "1F 大堂",       "LOBBY",         False),
        (2, "2F 教室",       "CLASSROOM",     False),
        (3, "3F 教室",       "CLASSROOM",     False),
        (4, "4F 机房",       "MECHANICAL",    True),
    ],
    "Bobcat_assembly_Franklin": [
        (1, "1F 体育场馆",   "SPORTS",        False),
        (2, "2F 大堂",       "LOBBY",         False),
        (3, "3F 机房",       "MECHANICAL",    True),
    ],
    "Bobcat_science_Tammy": [
        (1, "1F 大堂",       "LOBBY",         False),
        (2, "2F 实验室",     "LAB",           False),
        (3, "3F 实验室",     "LAB",           False),
        (4, "4F 办公",       "OFFICE",        False),
        (5, "5F 办公",       "OFFICE",        False),
        (6, "6F 机房",       "MECHANICAL",    True),
    ],
    "Bobcat_public_Angie": [
        (1, "1F 阅览大厅",   "LOBBY",         False),
        (2, "2F 书库",       "LIBRARY",       False),
        (3, "3F 办公",       "OFFICE",        False),
        (4, "4F 机房",       "MECHANICAL",    True),
    ],
}

# 楼栋真实 floors_count (来自 BDG2 metadata) - 用于判断是否需要虚拟扩展
REAL_FLOORS_COUNT = {
    "Bobcat_education_Alissa": 2,    # 扩展到 5
    "Bobcat_education_Dylan": 2,
    "Bobcat_education_Seth": 4,
    "Bobcat_assembly_Franklin": 1,   # 扩展到 3
    "Bobcat_science_Tammy": 6,
    "Bobcat_public_Angie": 2,        # 扩展到 4 (Library 适合分层)
}


# ============================================================================
# usage_factor 表: (floor_type, energy_type) -> 权重系数 (归一化前)
# ----------------------------------------------------------------------------
# 基于工程经验: LOBBY 人流大电力+水高, LAB 设备多能耗高, MECHANICAL 机房 24h
# 跑电力最高, LIBRARY 安静能耗低, SPORTS 更衣洗澡水高。
# 归一化后 Σ w_norm = 1, 保证加总约束成立。
# ============================================================================
USAGE_FACTOR = {
    ('LOBBY',          'electricity'):   1.2,
    ('LOBBY',          'hotwater'):      1.0,
    ('LOBBY',          'chilledwater'):  1.2,
    ('LOBBY',          'water'):         1.5,
    ('CLASSROOM',      'electricity'):   1.0,
    ('CLASSROOM',      'hotwater'):      1.0,
    ('CLASSROOM',      'chilledwater'):  1.0,
    ('CLASSROOM',      'water'):         1.0,
    ('OFFICE',         'electricity'):   1.0,
    ('OFFICE',         'hotwater'):      0.8,
    ('OFFICE',         'chilledwater'):  1.0,
    ('OFFICE',         'water'):         0.8,
    ('LAB',            'electricity'):   1.5,
    ('LAB',            'hotwater'):      1.3,
    ('LAB',            'chilledwater'):  1.3,
    ('LAB',            'water'):         1.2,
    ('LAB',            'gas'):           1.0,
    ('MECHANICAL',     'electricity'):   2.0,
    ('MECHANICAL',     'hotwater'):      1.5,
    ('MECHANICAL',     'chilledwater'):  1.8,
    ('MECHANICAL',     'water'):         0.5,
    ('MECHANICAL',     'gas'):           1.0,
    ('LIBRARY',        'electricity'):   0.8,
    ('LIBRARY',        'hotwater'):      0.5,
    ('LIBRARY',        'chilledwater'):  0.8,
    ('LIBRARY',        'water'):         0.5,
    ('SPORTS',         'electricity'):   1.3,
    ('SPORTS',         'hotwater'):      1.5,
    ('SPORTS',         'chilledwater'):  1.2,
    ('SPORTS',         'water'):         2.0,
    ('SPORTS',         'gas'):           1.0,
    ('STUDENT_CENTER', 'electricity'):   1.3,
    ('STUDENT_CENTER', 'hotwater'):      1.2,
    ('STUDENT_CENTER', 'chilledwater'):  1.2,
    ('STUDENT_CENTER', 'water'):         1.5,
}


# ============================================================================
# 房间布局模板: floor_type -> {rooms: [...], schedule: "..."}
# ----------------------------------------------------------------------------
# 前端 IsometricFloor.vue 直接读 rooms 数组画 SVG 等距投影。坐标用网格单位
# (1 单位 = 1.5m), 前端乘 1.5 转米。同 floor_type 的不同楼层用同一套模板,
# 通过 area_sqm 缩放视觉大小。
#
# 每个房间: {name, x, y, w, h, usage}
#   x/y: 左下角网格坐标 (0,0 = 左下)
#   w/h: 宽/高 (网格单位)
#   usage: 房间用途, 决定家具图标 (CLASSROOM 放桌椅, LAB 放烧瓶显微镜, ...)
# ============================================================================
ROOM_TEMPLATES = {
    'LOBBY': {
        'rooms': [
            {'name': '大堂区', 'x': 0, 'y': 0, 'w': 6, 'h': 4, 'usage': 'LOBBY'},
            {'name': '接待台', 'x': 6, 'y': 0, 'w': 2, 'h': 2, 'usage': 'OFFICE'},
            {'name': '休息区', 'x': 6, 'y': 2, 'w': 2, 'h': 2, 'usage': 'LOBBY'},
            {'name': '电梯厅', 'x': 0, 'y': 4, 'w': 8, 'h': 1, 'usage': 'OTHER'},
        ],
        'schedule': '7-22 weekday',
    },
    'CLASSROOM': {
        'rooms': [
            {'name': '教室A', 'x': 0, 'y': 0, 'w': 4, 'h': 3, 'usage': 'CLASSROOM'},
            {'name': '教室B', 'x': 4, 'y': 0, 'w': 4, 'h': 3, 'usage': 'CLASSROOM'},
            {'name': '走廊',  'x': 0, 'y': 3, 'w': 8, 'h': 1, 'usage': 'OTHER'},
            {'name': '教室C', 'x': 0, 'y': 4, 'w': 4, 'h': 3, 'usage': 'CLASSROOM'},
            {'name': '教室D', 'x': 4, 'y': 4, 'w': 4, 'h': 3, 'usage': 'CLASSROOM'},
        ],
        'schedule': '8-17 weekday',
    },
    'OFFICE': {
        'rooms': [
            {'name': '办公区A', 'x': 0, 'y': 0, 'w': 5, 'h': 4, 'usage': 'OFFICE'},
            {'name': '办公区B', 'x': 5, 'y': 0, 'w': 3, 'h': 4, 'usage': 'OFFICE'},
            {'name': '会议室',  'x': 0, 'y': 4, 'w': 4, 'h': 3, 'usage': 'OFFICE'},
            {'name': '茶水间',  'x': 4, 'y': 4, 'w': 4, 'h': 3, 'usage': 'OTHER'},
        ],
        'schedule': '9-18 weekday',
    },
    'LAB': {
        'rooms': [
            {'name': '实验室A', 'x': 0, 'y': 0, 'w': 5, 'h': 4, 'usage': 'LAB'},
            {'name': '实验室B', 'x': 5, 'y': 0, 'w': 3, 'h': 4, 'usage': 'LAB'},
            {'name': '设备间',  'x': 0, 'y': 4, 'w': 4, 'h': 3, 'usage': 'MECHANICAL'},
            {'name': '更衣室',  'x': 4, 'y': 4, 'w': 4, 'h': 3, 'usage': 'OTHER'},
        ],
        'schedule': '24h',
    },
    'MECHANICAL': {
        'rooms': [
            {'name': '主机房', 'x': 0, 'y': 0, 'w': 6, 'h': 5, 'usage': 'MECHANICAL'},
            {'name': '配电室', 'x': 6, 'y': 0, 'w': 2, 'h': 3, 'usage': 'MECHANICAL'},
            {'name': '备件库', 'x': 6, 'y': 3, 'w': 2, 'h': 2, 'usage': 'OTHER'},
        ],
        'schedule': '24h',
    },
    'LIBRARY': {
        'rooms': [
            {'name': '阅览区', 'x': 0, 'y': 0, 'w': 6, 'h': 5, 'usage': 'LIBRARY'},
            {'name': '书库',   'x': 6, 'y': 0, 'w': 2, 'h': 5, 'usage': 'LIBRARY'},
            {'name': '自习室', 'x': 0, 'y': 5, 'w': 5, 'h': 2, 'usage': 'LIBRARY'},
            {'name': '服务台', 'x': 5, 'y': 5, 'w': 3, 'h': 2, 'usage': 'OFFICE'},
        ],
        'schedule': '8-22 weekday',
    },
    'SPORTS': {
        'rooms': [
            {'name': '场馆区', 'x': 0, 'y': 0, 'w': 8, 'h': 5, 'usage': 'SPORTS'},
            {'name': '更衣室', 'x': 0, 'y': 5, 'w': 4, 'h': 2, 'usage': 'OTHER'},
            {'name': '器材室', 'x': 4, 'y': 5, 'w': 4, 'h': 2, 'usage': 'OTHER'},
        ],
        'schedule': '6-22 weekday',
    },
    'STUDENT_CENTER': {
        'rooms': [
            {'name': '活动区', 'x': 0, 'y': 0, 'w': 5, 'h': 4, 'usage': 'STUDENT_CENTER'},
            {'name': '餐饮区', 'x': 5, 'y': 0, 'w': 3, 'h': 4, 'usage': 'STUDENT_CENTER'},
            {'name': '会议室', 'x': 0, 'y': 4, 'w': 4, 'h': 3, 'usage': 'OFFICE'},
            {'name': '休息区', 'x': 4, 'y': 4, 'w': 4, 'h': 3, 'usage': 'LOBBY'},
        ],
        'schedule': '7-22 weekday',
    },
    'OTHER': {
        'rooms': [
            {'name': '通用区', 'x': 0, 'y': 0, 'w': 8, 'h': 7, 'usage': 'OTHER'},
        ],
        'schedule': '24h',
    },
}


# 8 种能源的 measure_kind (跟 bdg2.py ENERGY_TYPE_TO_MEASURE 对齐)
ENERGY_MEASURE = {
    'electricity':   ('energy_kwh',         'kWh'),
    'hotwater':      ('energy_kwh_thermal', 'kWh_thermal'),
    'chilledwater':  ('energy_kwh_thermal', 'kWh_thermal'),
    'steam':         ('energy_kwh_thermal', 'kWh_thermal'),
    'gas':           ('energy_kwh',         'kWh'),
    'water':         ('volume_liter',       'L'),
    'irrigation':    ('volume_liter',       'L'),
    'solar':         ('energy_kwh',         'kWh'),
}

# 加总约束只对能源类 (kWh) 强制, 体积类 (water L) 也强制但单位不同
# 这两类都走 enforce_sum_constraint, 不区分
ENERGY_TYPES_FOR_SUM = {'electricity', 'hotwater', 'chilledwater', 'steam', 'gas', 'water', 'solar'}

# 2017 全年小时数
HOURS_2017 = 8760


def reset_floor_data(tenant_id: str) -> None:
    """
    清空 demo 租户的楼层相关数据, 保留楼栋级 METER 数据。

    清理范围 (按依赖顺序):
    - mart.point_status_snapshot (楼层 SENSOR 的状态)
    - mart.floor_daily_energy (楼层日聚合)
    - fact.point_reading (楼层 SENSOR 的读数, WHERE source_dataset='synthetic_floor')
    - core.point (楼层 SENSOR, WHERE source_dataset='synthetic_floor')
    - core.floor (楼层实体)

    不清:
    - 楼栋级 METER point 和读数 (source_dataset='bdg2')
    - mart.building_daily_energy (楼栋日聚合)
    """
    logger.info("清空 demo 租户的楼层虚拟数据 (保留楼栋级 METER)...")
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 先删楼层 SENSOR 的状态 (按 source_dataset 过滤, 不删楼栋 METER 的)
            cur.execute("""
                DELETE FROM mart.point_status_snapshot
                WHERE tenant_id = %s
                  AND point_id IN (
                      SELECT id FROM core.point
                      WHERE source_dataset = 'synthetic_floor'
                  )
            """, (tenant_id,))
            logger.info("  mart.point_status_snapshot 已清 (楼层部分)")

            # 楼层级 fact.point_reading 通过 point_id CASCADE 删
            # 但 fact.point_reading 没有 source_dataset, 要通过 point JOIN 拿
            cur.execute("""
                DELETE FROM fact.point_reading
                WHERE tenant_id = %s
                  AND point_id IN (
                      SELECT id FROM core.point
                      WHERE source_dataset = 'synthetic_floor'
                  )
            """, (tenant_id,))
            logger.info("  fact.point_reading 已清 (楼层部分)")

            cur.execute("DELETE FROM mart.floor_daily_energy WHERE tenant_id = %s", (tenant_id,))
            logger.info("  mart.floor_daily_energy 已清")

            cur.execute("DELETE FROM core.point WHERE source_dataset = 'synthetic_floor' AND tenant_id = %s", (tenant_id,))
            logger.info("  core.point 已清 (synthetic_floor)")

            cur.execute("DELETE FROM core.floor WHERE source_dataset = 'synthetic_floor' AND tenant_id = %s", (tenant_id,))
            logger.info("  core.floor 已清")
        conn.commit()
    logger.info("楼层虚拟数据清空完成")


def generate_floor_shape(floor_type: str, ts_list: list) -> np.ndarray:
    """
    生成楼层的 shape 时序调制系数, 长度 = len(ts_list), 均值严格归一化到 1.0。

    shape 基于每个 ts 自带的时区 (BDG2 数据存 +08:00, shape 用北京时间的
    hour/weekday/month)。不同 floor_type 走不同 shape 模板:
      OFFICE:        工作日 9-18 高, 夜间低, 周末低
      CLASSROOM:     8-17 高, 夜间零, 周末零, 暑假(6-8月)整体降低
      LAB:           24h 持续偏高, 夜间略降, 周末略降
      MECHANICAL:    24h 平稳 (机房 24h 跑)
      LOBBY:         7-22 中高, 夜间零
      LIBRARY:       8-22 中, 夜间零
      SPORTS:        早晚高峰(6-9, 17-22), 夜间零
      STUDENT_CENTER:7-22 中高, 夜间零
      OTHER:         平稳 1.0

    均值归一化保证 shape 不改变总能量 (Σ shape[i] / N = 1), 加总约束在
    enforce_sum_constraint 里独立保证。

    为什么用 ts_list 不用固定 8760:
      BDG2 数据存 +08:00 时区 (2017-01-01 00:00+08 起), 跟 UTC 差 8 小时。
      旧版按 UTC 起点 + h 小时生成 shape, 时序错位 8 小时, 导致 floor 数据
      跟 building 数据对不上 (floor_total 远大于 building_total)。改用 ts_list
      直接拿 ts.hour/weekday/month, 保证 shape 跟 building 数据同一时区。
    """
    n = len(ts_list)
    shape = np.ones(n, dtype=np.float64)

    for i, ts in enumerate(ts_list):
        hour = ts.hour
        dow = ts.weekday()  # 0=Mon, 6=Sun
        month = ts.month
        is_weekday = dow < 5
        is_summer = month in (6, 7, 8)

        if floor_type == 'OFFICE':
            if is_weekday and 9 <= hour < 18:
                shape[i] = 1.3
            elif is_weekday and (8 <= hour < 9 or 18 <= hour < 20):
                shape[i] = 0.9
            else:
                shape[i] = 0.4 if is_weekday else 0.3
        elif floor_type == 'CLASSROOM':
            if is_summer:
                shape[i] = 0.3
            elif is_weekday and 8 <= hour < 17:
                shape[i] = 1.4
            elif is_weekday and (7 <= hour < 8 or 17 <= hour < 19):
                shape[i] = 0.8
            else:
                shape[i] = 0.2 if is_weekday else 0.1
        elif floor_type == 'LAB':
            if is_weekday:
                shape[i] = 1.1 if 8 <= hour < 18 else 0.95
            else:
                shape[i] = 0.9 if 8 <= hour < 18 else 0.85
        elif floor_type == 'MECHANICAL':
            shape[i] = 1.0
        elif floor_type == 'LOBBY':
            if is_weekday and 7 <= hour < 22:
                shape[i] = 1.2
            else:
                shape[i] = 0.3
        elif floor_type == 'LIBRARY':
            if is_weekday and 8 <= hour < 22:
                shape[i] = 1.1
            elif not is_weekday and 9 <= hour < 21:
                shape[i] = 0.9
            else:
                shape[i] = 0.2
        elif floor_type == 'SPORTS':
            if is_weekday and (6 <= hour < 9 or 17 <= hour < 22):
                shape[i] = 1.4
            elif not is_weekday and 8 <= hour < 22:
                shape[i] = 1.2
            else:
                shape[i] = 0.3
        elif floor_type == 'STUDENT_CENTER':
            if is_weekday and 7 <= hour < 22:
                shape[i] = 1.2
            elif not is_weekday and 9 <= hour < 21:
                shape[i] = 1.0
            else:
                shape[i] = 0.3
        # OTHER 保持 1.0

    mean = shape.mean()
    if mean > 0:
        shape = shape / mean
    return shape


def enforce_sum_constraint(
    floor_curves: list[np.ndarray],
    building_curve: np.ndarray,
    floor_areas: list[float],
) -> list[np.ndarray]:
    """
    逐小时按比例缩放, 保证 Σ floor_curves[i][t] = building_curve[t]。

    数学上严格成立, 误差仅浮点级 1e-15。

    边界场景:
      building=0 且 raw_sum=0:  全 0, OK
      building>0 但 raw_sum=0:  按面积均分 (极端情况, shape 全零的楼层)
      building=0 但 raw_sum>0:  全部置零 (楼栋没能耗, 楼层也不该有)
    """
    if not floor_curves:
        return []

    raw_sum = np.sum(floor_curves, axis=0)
    result = [fc.copy() for fc in floor_curves]

    # 正常: raw_sum > 0, 按比例缩放
    mask_normal = raw_sum > 0
    if mask_normal.any():
        scale = np.zeros_like(building_curve)
        scale[mask_normal] = building_curve[mask_normal] / raw_sum[mask_normal]
        for i in range(len(result)):
            result[i] = result[i] * scale

    # 边界: building>0 但 raw_sum=0, 按面积均分
    mask_fallback = (raw_sum == 0) & (building_curve > 0)
    if mask_fallback.any():
        total_area = sum(floor_areas)
        for i, area in enumerate(floor_areas):
            w = area / total_area if total_area > 0 else 1.0 / len(floor_areas)
            result[i][mask_fallback] = building_curve[mask_fallback] * w

    # 边界: building=0 但 raw_sum>0, 全部置零
    mask_zero = (building_curve == 0) & (raw_sum > 0)
    if mask_zero.any():
        for i in range(len(result)):
            result[i][mask_zero] = 0

    return result


def simulate_device_fault(
    curve: np.ndarray,
    rng: np.random.Generator,
) -> tuple[str, str | None]:
    """
    模拟设备故障, 直接修改 curve (置零部分数据)。

    故障率: 2% FAULT + 2% OFFLINE + 4% STALE + 92% ONLINE
    固定 seed 可复现, 每栋楼分布在不同能源和楼层。

    为什么从 plan 的 0.5/0.5/1 提到 2/2/4:
      87 个 SENSOR point, plan 的 2% 总故障率期望 1.74 个故障, 实际跑出来 0 个
      (P(0 故障) = 0.98^87 ≈ 17%, 不算极端但 demo 看不到故障没意义)。提到 8%
      总故障率期望 ~7 个故障, 每栋楼 1-2 个, 跟 plan 的"每栋楼 1-2 个"对齐。

    返回 (status, fault_reason)。
    故障 point 数据被置零后, enforce_sum_constraint 归一化时其他楼层会自动补上,
    加总约束不失效 (1 层能源跳过故障, 见 run_floor_synthetic_seed)。
    """
    r = rng.random()
    if r < 0.02:
        curve[:] = 0
        return 'FAULT', 'ZERO_FILL'
    if r < 0.04:
        start = int(rng.integers(0, max(1, len(curve) - 24)))
        curve[start:start + 24] = 0
        return 'OFFLINE', 'NO_DATA_24H'
    if r < 0.08:
        mask = rng.random(len(curve)) < 0.5
        curve[mask] = 0
        return 'STALE', None
    return 'ONLINE', None


def _stable_seed(*parts) -> int:
    """把多个参数 hash 成稳定整数 seed (可复现)。"""
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _default_floor_plan(floors_count: int) -> list[tuple[int, str, str, bool]]:
    """
    按 floors_count 自动生成默认楼层用途分配 (auto-split 用)。

    规则:
      1 层:  MECHANICAL (rooftop) - 单层只能是机房/设备间
      2 层:  1F LOBBY + 2F MECHANICAL (rooftop)
      3+ 层: 1F LOBBY / 中间 OFFICE / 顶层 MECHANICAL (rooftop)

    跟 FLOOR_PLAN 里 BDG2 真实楼栋的分配思路一致 (底层大堂, 中间办公/教室,
    顶层机房), 但更简化 (不分 CLASSROOM / LAB, 用户后续可手动改 floor_type)。

    返回 [(floor_number, floor_name, floor_type, is_rooftop), ...]
    """
    if floors_count < 1:
        raise ValueError("floors_count 必须 >= 1")

    if floors_count == 1:
        return [(1, "1F 机房", "MECHANICAL", True)]

    plan = [(1, "1F 大堂", "LOBBY", False)]
    for fnum in range(2, floors_count):
        plan.append((fnum, f"{fnum}F 办公", "OFFICE", False))
    plan.append((floors_count, f"{floors_count}F 机房", "MECHANICAL", True))
    return plan


def process_building_floors(
    cur,
    tenant_id: str,
    bid: str,
    bcode: str,
    sqm: float,
    real_floors: int,
    floor_defs: list[tuple[int, str, str, bool]],
    source_dataset: str = "synthetic_floor",
) -> dict:
    """
    处理单栋楼: 写楼层实体 + 生成 SENSOR 读数 + 灌 mart 聚合表 + 设备状态。

    被两处调用:
      - run_floor_synthetic_seed (本文件): demo 数据 seed, source_dataset='synthetic_floor'
      - floor_service.auto_split_floors: 用户点"自动拆分"按钮, source_dataset='auto_split'

    不 commit, 调用方负责事务边界 (seed 楼栋粒度 commit; auto_split 整楼一次 commit)。

    前置条件:
      - building 已存在 (bid 有效)
      - 楼栋级 METER 数据已就位 (fact.point_reading 里有 source_dataset='bdg2'
        或 'user_upload' 的楼栋总表读数), 否则没有 ground truth 可拆分

    Args:
        cur: psycopg2 cursor
        tenant_id: 租户 UUID
        bid: 楼栋 UUID
        bcode: building_code (用作 point_code 前缀, 例如 "Bobcat_education_Alissa")
        sqm: 楼栋总面积
        real_floors: 当前 core.building.floors_count (跟 floor_defs 长度不一致时
                     会 UPDATE 成 floor_defs 的长度)
        floor_defs: [(floor_number, floor_name, floor_type, is_rooftop), ...]
        source_dataset: 'synthetic_floor' 或 'auto_split', 写到 core.floor /
                        core.point / ingest.import_batch 的 source 字段

    Returns:
        {
            "floors_created": int,
            "floor_points_created": int,
            "floor_readings_inserted": int,
            "floor_daily_rows": int,
            "device_status_rows": int,
        }
    """
    stats = {
        "floors_created": 0,
        "floor_points_created": 0,
        "floor_readings_inserted": 0,
        "floor_daily_rows": 0,
        "device_status_rows": 0,
    }

    n_floors = len(floor_defs)

    # 楼层扩展: 更新 building.floors_count + source_ref
    if n_floors != real_floors:
        cur.execute("""
            UPDATE core.building
            SET floors_count = %s,
                source_ref = %s
            WHERE id = %s
        """, (n_floors, f"extended_from_{real_floors}", bid))
        logger.info("  楼层扩展: {} -> {}", real_floors, n_floors)

    # 写 core.floor (按总面积均分层面积)
    area_per_floor = float(sqm) / n_floors
    floor_id_map = {}  # floor_number -> floor_id

    # 一栋楼一个 site_id, 循环外查一次
    cur.execute("SELECT site_id FROM core.building WHERE id = %s", (bid,))
    site_id = cur.fetchone()[0]

    for fnum, fname, ftype, is_rooftop in floor_defs:
        template = ROOM_TEMPLATES.get(ftype, ROOM_TEMPLATES['OTHER'])
        metadata = json.dumps(template, ensure_ascii=False)
        cur.execute("""
            INSERT INTO core.floor
                (tenant_id, site_id, building_id, floor_number, floor_name,
                 floor_type, area_sqm, is_rooftop, source_dataset, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (building_id, floor_number) DO UPDATE SET
                floor_name = EXCLUDED.floor_name,
                floor_type = EXCLUDED.floor_type,
                area_sqm = EXCLUDED.area_sqm,
                is_rooftop = EXCLUDED.is_rooftop,
                metadata = EXCLUDED.metadata,
                source_dataset = EXCLUDED.source_dataset
            RETURNING id
        """, (tenant_id, site_id, bid, fnum, fname, ftype,
              area_per_floor, is_rooftop, source_dataset, metadata))
        floor_id_map[fnum] = cur.fetchone()[0]

    stats["floors_created"] += len(floor_id_map)
    logger.info("  core.floor 写入: {} 层", len(floor_id_map))

    # 读楼栋级 ground truth: 每种能源的 (ts, value) 曲线
    # 楼栋 METER 的 source_dataset 是 'bdg2' (demo seed) 或 'user_upload' (用户上传),
    # 都查出来。point_kind='METER' 排除掉其他楼栋级 point。
    cur.execute("""
        SELECT p.energy_type, p.id, pr.ts, pr.value_num
        FROM fact.point_reading pr
        JOIN core.point p ON p.id = pr.point_id
        WHERE p.building_id = %s
          AND p.point_kind = 'METER'
          AND p.source_dataset IN ('bdg2', 'user_upload')
        ORDER BY p.energy_type, pr.ts
    """, (bid,))
    rows = cur.fetchall()

    if not rows:
        raise ValueError(
            f"楼栋 {bcode} 没有楼栋级 METER 读数 (fact.point_reading), "
            "请先上传楼栋能耗读数再拆分楼层"
        )

    # 组织成 {energy_type: [(ts, value), ...]}
    meter_data = defaultdict(list)  # energy_type -> [(ts, value), ...]
    for et, pid, ts, val in rows:
        meter_data[et].append((ts, float(val) if val is not None else 0.0))

    # 转成 (ts_list, curve) 元组, 按 ts 升序。
    # 关键: ts_list 保留 building 数据原始时区 (BDG2 是 +08:00), floor
    # 读数直接用 ts_list[i] 做 ts, 保证 floor 跟 building 同一小时对齐。
    # 旧版按 UTC 2017-01-01 00:00 起点写 floor ts, 跟 +08:00 building
    # 数据差 8 小时, 导致加总约束校验 floor_total 远大于 building_total。
    building_curves = {}  # energy_type -> (ts_list, np.ndarray)
    for et, pairs in meter_data.items():
        pairs.sort(key=lambda x: x[0])
        ts_list = [p[0] for p in pairs]
        curve = np.array([p[1] for p in pairs], dtype=np.float64)
        building_curves[et] = (ts_list, curve)
        if len(pairs) != HOURS_2017:
            logger.warning("  楼栋 {} 能源 {} 读数 {} 行 (期望 {})",
                           bcode, et, len(pairs), HOURS_2017)

    # 每种能源生成楼层数据
    # 预建 floors 列表: [(floor_number, floor_type, is_rooftop, floor_id), ...]
    floors = [(fnum, ftype, is_rooftop, floor_id_map[fnum])
              for fnum, _, ftype, is_rooftop in floor_defs]

    # floor_points 多带一个 ts_list 字段 (写 staging 时按 ts_list 对齐 building)
    floor_points = []  # [(floor_id, floor_number, floor_type, energy_type, point_id, curve, status, fault_reason, ts_list)]
    for et, (ts_list, building_curve) in building_curves.items():
        if et not in ENERGY_MEASURE:
            logger.warning("  未知能源类型 {}, 跳过", et)
            continue

        n_hours = len(ts_list)

        # 决定哪些楼层有这个能源
        if et == 'solar':
            floors_with_et = [f for f in floors if f[2]]  # is_rooftop
        elif et == 'gas':
            floors_with_et = [f for f in floors
                              if f[1] in ('LAB', 'MECHANICAL', 'SPORTS')]
        else:
            floors_with_et = floors

        if not floors_with_et:
            continue

        if et == 'solar':
            # solar 顶层 1:1 复制, 不归一化, 不故障 (1 层无法补偿故障零值,
            # 强行故障会破坏加总约束)
            fn, ft, ir, fid = floors_with_et[0]
            curve = building_curve.copy()
            floor_points.append((fid, fn, ft, et, None, curve,
                                 'ONLINE', None, ts_list))
            continue

        # 标准流程: 算权重 + shape + noise + 故障 + 归一化
        weights = []
        floor_areas = []
        for fn, ft, ir, fid in floors_with_et:
            factor = USAGE_FACTOR.get((ft, et), 1.0)
            w = area_per_floor * factor
            weights.append(w)
            floor_areas.append(area_per_floor)

        total_w = sum(weights)
        w_norm = [w / total_w for w in weights] if total_w > 0 else [1.0 / len(weights)] * len(weights)

        # 生成每层 raw curve, 先套故障再归一化 (让其他楼层补偿故障零值)
        raw_curves = []
        statuses = []  # [(status, fault)] per floor
        for i, (fn, ft, ir, fid) in enumerate(floors_with_et):
            shape = generate_floor_shape(ft, ts_list)
            rng = np.random.default_rng(_stable_seed(bcode, fid, et))
            noise = 1 + rng.normal(0, 0.03, n_hours)
            raw = building_curve * w_norm[i] * shape * noise
            raw = np.maximum(raw, 0)

            # 1 层能源不故障: 故障零值无法被其他楼层补偿, 归一化会被
            # fallback 覆盖 (status=FAULT 但 curve=building, 不一致)
            if len(floors_with_et) == 1:
                statuses.append(('ONLINE', None))
            else:
                rng_fault = np.random.default_rng(_stable_seed(bcode, fid, et, "fault"))
                status, fault = simulate_device_fault(raw, rng_fault)
                statuses.append((status, fault))

            raw_curves.append(raw)

        # 加总约束归一化: 故障零值由其他楼层自动补偿
        normed = enforce_sum_constraint(raw_curves, building_curve, floor_areas)

        # 收集
        for i, (fn, ft, ir, fid) in enumerate(floors_with_et):
            curve = normed[i].copy()
            status, fault = statuses[i]
            floor_points.append((fid, fn, ft, et, None, curve,
                                 status, fault, ts_list))

    # 写楼层 SENSOR point (拿到 point_id 回填到 floor_points)
    for i, (fid, fn, ft, et, _, curve, status, fault, ts_list) in enumerate(floor_points):
        measure_kind, unit_code = ENERGY_MEASURE[et]
        point_code = f"{bcode}__F{fn}__{et}"
        point_name = f"{bcode} - {fn}F - {et}"
        cur.execute("""
            INSERT INTO core.point
                (tenant_id, site_id, building_id, floor_id, point_code, point_name,
                 point_kind, energy_type, measure_kind, unit_code,
                 sample_interval_sec, source_dataset, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, 'SENSOR', %s, %s, %s, 3600,
                    %s, %s::jsonb)
            ON CONFLICT (tenant_id, point_code) DO UPDATE SET
                floor_id = EXCLUDED.floor_id,
                point_name = EXCLUDED.point_name,
                source_dataset = EXCLUDED.source_dataset
            RETURNING id
        """, (tenant_id, site_id, bid, fid, point_code, point_name,
              et, measure_kind, unit_code, source_dataset,
              json.dumps({"synthetic": True, "generated_by": "floor_synthetic.py",
                          "version": "1.0", "source": source_dataset}, ensure_ascii=False)))
        pid = cur.fetchone()[0]
        # 更新 floor_points 里的 point_id
        floor_points[i] = (fid, fn, ft, et, pid, curve, status, fault, ts_list)

    stats["floor_points_created"] += len(floor_points)
    logger.info("  core.point 写入: {} 个 SENSOR", len(floor_points))

    # COPY 灌 staging_reading
    # ts 用 building 数据的 ts_list (保留 +08:00 时区), 跟 building 同小时对齐
    cur.execute("""
        INSERT INTO ingest.import_batch
            (tenant_id, dataset_source, target_type, status,
             row_count_total, row_count_success, started_at, finished_at)
        VALUES (%s, %s, 'POINT', 'LOADING', 0, 0, now(), now())
        RETURNING id
    """, (tenant_id, source_dataset))
    reading_batch_id = cur.fetchone()[0]

    buf = StringIO()
    writer = csv.writer(buf, delimiter="\t")
    row_no = 0
    for fid, fn, ft, et, pid, curve, status, fault, ts_list in floor_points:
        for h, ts in enumerate(ts_list):
            row_no += 1
            writer.writerow([
                reading_batch_id, row_no, "Bobcat", bcode,
                f"{bcode}__F{fn}__{et}",
                ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                f"{curve[h]:.6f}",
                ENERGY_MEASURE[et][1],
                "GOOD" if curve[h] > 0 or status == 'ONLINE' else "MISSING",
            ])
    buf.seek(0)
    cur.copy_from(buf, "staging_reading", sep="\t", null="",
                  columns=("batch_id", "row_no", "site_code", "building_code",
                           "point_code", "ts_text", "value_text", "unit_text", "quality_text"))

    cur.execute("""
        UPDATE ingest.import_batch
        SET row_count_total = %s, row_count_success = %s,
            status = 'SUCCEEDED', finished_at = now()
        WHERE id = %s
    """, (row_no, row_no, reading_batch_id))
    logger.info("  staging_reading 灌入: {} 行", row_no)

    # merge staging -> fact.point_reading
    cur.execute("""
        INSERT INTO fact.point_reading (
            tenant_id, site_id, building_id, point_id, ts,
            value_num, quality_code, source_batch_id
        )
        SELECT
            ib.tenant_id, p.site_id, p.building_id, p.id,
            s.ts_text::timestamptz, s.value_text::double precision,
            COALESCE(NULLIF(s.quality_text, ''), 'GOOD'), ib.id
        FROM ingest.staging_reading s
        JOIN ingest.import_batch ib ON ib.id = s.batch_id
        JOIN core.point p
          ON p.tenant_id = ib.tenant_id
         AND p.point_code = s.point_code
        WHERE s.batch_id = %s
        ON CONFLICT (point_id, ts) DO UPDATE
        SET value_num = EXCLUDED.value_num,
            quality_code = EXCLUDED.quality_code,
            source_batch_id = EXCLUDED.source_batch_id,
            updated_at = now()
    """, (reading_batch_id,))
    stats["floor_readings_inserted"] += cur.rowcount
    logger.info("  fact.point_reading 写入: {} 行", cur.rowcount)

    # 生成 mart.floor_daily_energy
    cur.execute("""
        INSERT INTO mart.floor_daily_energy
            (tenant_id, building_id, floor_id, energy_type, date,
             total_kwh, max_kwh, min_kwh, avg_kwh, hour_count,
             eui_kwh_per_m2, source_batch_id)
        SELECT
            p.tenant_id, p.building_id, p.floor_id, p.energy_type,
            pr.ts::date AS date,
            SUM(pr.value_num), MAX(pr.value_num),
            MIN(pr.value_num), AVG(pr.value_num),
            COUNT(*),
            ROUND(CAST(SUM(pr.value_num) / NULLIF(f.area_sqm, 0) AS numeric), 3),
            %s
        FROM fact.point_reading pr
        JOIN core.point p ON p.id = pr.point_id
        JOIN core.floor f ON f.id = p.floor_id
        WHERE pr.source_batch_id = %s
          AND p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal', 'volume_liter')
        GROUP BY p.tenant_id, p.building_id, p.floor_id, p.energy_type, pr.ts::date, f.area_sqm
        ON CONFLICT (floor_id, energy_type, date) DO UPDATE
        SET total_kwh = EXCLUDED.total_kwh,
            max_kwh = EXCLUDED.max_kwh,
            min_kwh = EXCLUDED.min_kwh,
            avg_kwh = EXCLUDED.avg_kwh,
            hour_count = EXCLUDED.hour_count,
            eui_kwh_per_m2 = EXCLUDED.eui_kwh_per_m2,
            source_batch_id = EXCLUDED.source_batch_id
    """, (reading_batch_id, reading_batch_id))
    stats["floor_daily_rows"] += cur.rowcount
    logger.info("  floor_daily_energy 写入: {} 行", cur.rowcount)

    # 灌 mart.point_status_snapshot
    for fid, fn, ft, et, pid, curve, status, fault, ts_list in floor_points:
        n_hours = len(curve)
        last_idx = n_hours - 1
        while last_idx >= 0 and curve[last_idx] == 0:
            last_idx -= 1
        last_ts = ts_list[last_idx] if last_idx >= 0 else None
        last_val = float(curve[last_idx]) if last_idx >= 0 else None
        completeness = float((curve > 0).sum()) / n_hours * 100 if n_hours > 0 else 0.0

        cur.execute("""
            INSERT INTO mart.point_status_snapshot
                (point_id, tenant_id, building_id, floor_id, status,
                 last_reading_ts, last_reading_val, completeness_pct,
                 fault_reason, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (point_id) DO UPDATE
            SET status = EXCLUDED.status,
                last_reading_ts = EXCLUDED.last_reading_ts,
                last_reading_val = EXCLUDED.last_reading_val,
                completeness_pct = EXCLUDED.completeness_pct,
                fault_reason = EXCLUDED.fault_reason,
                updated_at = now()
        """, (pid, tenant_id, bid, fid, status, last_ts, last_val,
              completeness, fault))
    stats["device_status_rows"] += len(floor_points)
    logger.info("  point_status_snapshot 写入: {} 行", len(floor_points))

    # 清 staging (这栋楼的)
    cur.execute("DELETE FROM ingest.staging_reading WHERE batch_id = %s",
                (reading_batch_id,))

    return stats


def run_floor_synthetic_seed(reset: bool = False) -> dict:
    """
    跑楼层虚拟数据生成 seed (demo 租户, 6 栋楼)。

    流程:
      1. reset (可选): 清 demo 租户的楼层相关数据 (保留楼栋级 METER)
      2. 拿 demo tenant_id
      3. for each building (6 栋):
         - 按 FLOOR_PLAN 拿楼层定义 (Alissa 扩展到 5, Franklin 扩展到 3, Angie 扩展到 4)
         - 调 process_building_floors 写楼层 + SENSOR 读数 + mart 表
         - 楼栋粒度 commit (释放内存)
      4. ANALYZE

    返回统计 dict。
    """
    stats = {
        "buildings_processed": 0,
        "floors_created": 0,
        "floor_points_created": 0,
        "floor_readings_inserted": 0,
        "floor_daily_rows": 0,
        "device_status_rows": 0,
    }

    logger.info("=" * 60)
    logger.info("开始楼层虚拟数据生成 seed")
    logger.info("=" * 60)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SET search_path TO core, ingest, fact, mart, knowledge, public")

        # 1. 拿 demo tenant_id
        cur.execute("SELECT id FROM core.tenant WHERE tenant_code = 'demo'")
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("demo 租户不存在, 先跑 bdg2 seed")
        tenant_id = row[0]

        # 2. reset
        if reset:
            reset_floor_data(tenant_id)

        # 3. 拿 6 栋楼的 building_id + 能源类型
        cur.execute("""
            SELECT id, building_code, sqm, floors_count
            FROM core.building
            WHERE tenant_id = %s
            ORDER BY building_code
        """, (tenant_id,))
        buildings = cur.fetchall()
        logger.info("待处理楼栋数: {}", len(buildings))

        for bid, bcode, sqm, real_floors in buildings:
            logger.info("-" * 40)
            logger.info("处理楼栋: {} (sqm={}, 真实楼层={})", bcode, sqm, real_floors)

            if bcode not in FLOOR_PLAN:
                logger.warning("  FLOOR_PLAN 未定义, 跳过")
                continue

            floor_defs = FLOOR_PLAN[bcode]
            bstats = process_building_floors(
                cur, tenant_id, bid, bcode, sqm, real_floors,
                floor_defs, source_dataset="synthetic_floor",
            )
            for k, v in bstats.items():
                stats[k] += v

            stats["buildings_processed"] += 1
            # 楼栋粒度 commit, 释放内存
            conn.commit()
            logger.info("  楼栋 {} 提交完成", bcode)

        # 4. ANALYZE
        logger.info("ANALYZE 关键表 ...")
        cur.execute("ANALYZE core.floor")
        cur.execute("ANALYZE core.point")
        cur.execute("ANALYZE fact.point_reading")
        cur.execute("ANALYZE mart.floor_daily_energy")
        cur.execute("ANALYZE mart.point_status_snapshot")

        conn.commit()

    logger.info("=" * 60)
    logger.info("楼层虚拟数据 seed 完成, 统计:")
    for k, v in stats.items():
        logger.info("  {}: {}", k, v)
    logger.info("=" * 60)
    return stats
