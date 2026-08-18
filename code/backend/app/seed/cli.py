"""
seed 命令行入口。

用法:
    python -m app.seed.cli bdg2                  # 不清空,直接灌(idempotent,已存在就 upsert)
    python -m app.seed.cli bdg2 --reset          # 先清 demo 租户业务数据,再灌
    python -m app.seed.cli floor-synthetic       # 单跑楼层虚拟数据生成 (依赖 bdg2 已灌)
    python -m app.seed.cli floor-synthetic --reset  # 先清楼层虚拟数据再灌

跑通后控制台打印统计。
"""
import argparse
import sys

from loguru import logger

from app.core.logging import setup_logging
from app.db.session import init_pool, close_pool


def main():
    parser = argparse.ArgumentParser(description="BDG2 demo 数据 seed")
    parser.add_argument(
        "dataset",
        choices=["bdg2", "floor-synthetic"],
        help="数据集名: bdg2=楼栋级 (含自动楼层), floor-synthetic=只跑楼层虚拟数据",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先清空对应业务数据再灌 (bdg2 清 demo 全部, floor-synthetic 只清楼层)",
    )
    args = parser.parse_args()

    setup_logging()
    init_pool()

    try:
        if args.dataset == "bdg2":
            from app.seed.bdg2 import run_bdg2_seed
            stats = run_bdg2_seed(reset=args.reset)
        elif args.dataset == "floor-synthetic":
            from app.seed.floor_synthetic import run_floor_synthetic_seed
            stats = run_floor_synthetic_seed(reset=args.reset)
        else:
            logger.error("未知 dataset: {}", args.dataset)
            sys.exit(1)

        logger.info("=" * 60)
        logger.info("seed 完成,统计:")
        for k, v in stats.items():
            logger.info("  {}: {}", k, v)
        logger.info("=" * 60)
    except Exception as e:
        logger.exception("seed 失败: {}", e)
        sys.exit(1)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
