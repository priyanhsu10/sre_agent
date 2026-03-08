"""
Centralized logging configuration for SRE Agent.

Sets up:
- Daily rotating log files in ../logs/ (parallel to working directory)
- Console (stdout) handler
- Consistent format across all modules

Usage:
    from logging_config import setup_logging
    setup_logging(log_level="INFO")
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(log_level: str = "INFO") -> None:
    """
    Configure application-wide logging with daily rotating file handler.

    Log files are written to ../logs/ relative to this file's directory,
    i.e. parallel to the project working directory.

    Files rotate at midnight daily; last 30 days are retained.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Resolve logs directory: parallel to project root
    project_root = Path(__file__).parent
    logs_dir = project_root.parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_file = logs_dir / "sre_agent.log"

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    # Daily rotating file handler — rotates at midnight, keeps 30 days
    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=True,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    file_handler.setLevel(numeric_level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(numeric_level)

    # Apply to root logger so every module's getLogger(__name__) inherits it
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers if called more than once (e.g. during tests)
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
    else:
        root_logger.handlers.clear()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        f"Logging initialised | level={log_level} | file={log_file}"
    )
