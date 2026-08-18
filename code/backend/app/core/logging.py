"""
loguru 日志配置。

提示词要求：时间 | 级别 | 模块 | 消息；文件按天轮转，保留 7 天；
同时输出到控制台和文件。

loguru 默认 logger 有个 WARNING 级别的 stderr sink，先 remove 掉再
重新加，否则会重复输出。uvicorn 自己有 logger，这里拦截一下让它
走 loguru，免得日志格式两套。
"""
import logging
import sys
from loguru import logger

from app.core.config import settings


def setup_logging() -> None:
    """配置 loguru。应用启动时调一次。"""
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # 控制台，彩色
    logger.add(
        sys.stderr,
        format=log_format,
        level=settings.log_level,
        colorize=True,
        backtrace=True,
        diagnose=settings.env == "dev",
    )

    # 文件，按天轮转，保留 7 天。UTF-8 写入，避免 Windows 控制台编码坑。
    logger.add(
        settings.log_dir_path / "app.log",
        format=log_format,
        level=settings.log_level,
        rotation="00:00",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        backtrace=True,
        diagnose=settings.env == "dev",
    )

    # 把标准库 logging 桥到 loguru，这样 uvicorn / psycopg2 的日志也走同一套格式
    class InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            frame, depth = logging.currentframe(), 2
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1
            logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # uvicorn 内部有自己的 logger，单独覆盖
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(name).handlers = [InterceptHandler()]
        logging.getLogger(name).propagate = False

    logger.info("日志系统就绪：控制台 + 文件 {}（按天轮转，保留 7 天）", settings.log_dir_path / "app.log")
