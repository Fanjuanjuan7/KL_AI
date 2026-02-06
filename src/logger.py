"""
Logging configuration module for KL_AI application.

Provides centralized logging setup with console and file handlers,
supporting log rotation and configurable log levels.

Example:
    >>> from logger import setup_logger
    >>> logger = setup_logger("MyApp", level="DEBUG", log_dir="logs", max_bytes=10*1024*1024, backup_count=5)
    >>> logger.info("Application started")
"""

import logging
import logging.handlers
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union


class LevelFilter(logging.Filter):
    """Filter log records by level range."""

    def __init__(self, min_level: int = logging.NOTSET, max_level: int = logging.CRITICAL) -> None:
        super().__init__()
        self.min_level = min_level
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return self.min_level <= record.levelno <= self.max_level


def setup_logger(
    name: str = "KL_AI",
    level: Union[str, int] = logging.INFO,
    log_dir: Optional[str] = "logs",
    log_filename: Optional[str] = None,
    console_output: bool = True,
    file_output: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    formatter_string: Optional[str] = None
) -> logging.Logger:
    """
    Configure and return a logger with console and file handlers.

    Args:
        name: Logger name
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files (None to disable file logging)
        log_filename: Custom log filename (None for auto-generated)
        console_output: Whether to output logs to console
        file_output: Whether to output logs to file
        max_bytes: Maximum bytes per log file before rotation
        backup_count: Number of backup files to keep
        formatter_string: Custom log format string

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Convert string level to int if needed
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(level)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Default formatter
    if formatter_string is None:
        formatter_string = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    formatter = logging.Formatter(formatter_string)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        # Filter out DEBUG messages from console unless level is DEBUG
        if level > logging.DEBUG:
            console_handler.addFilter(LevelFilter(min_level=level))
        logger.addHandler(console_handler)

    # File handler with rotation
    if file_output and log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        if log_filename is None:
            log_filename = f"app_{datetime.now().strftime('%Y%m%d')}.log"

        log_file = log_path / log_filename

        # Use RotatingFileHandler for log rotation
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "KL_AI") -> logging.Logger:
    """
    Get an existing logger or create a basic one if not exists.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger
