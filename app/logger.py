"""统一日志格式"""
import sys
import io
from loguru import logger

# Windows 控制台默认 GBK，emoji/中文会爆 → 强制 UTF-8 包一层
_utf8_stdout = io.TextIOWrapper(
    sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
) if hasattr(sys.stdout, "buffer") else sys.stdout

# 移除默认 handler
logger.remove()

# 添加 stdout handler（强制 UTF-8）
logger.add(
    _utf8_stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
           "<level>{level:<8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
           "<level>{message}</level>",
    level="INFO",
)

# 错误日志单独文件（文件是 UTF-8，emoji 保留）
logger.add(
    "logs/error_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    level="ERROR",
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
)
