# -*- coding: utf-8 -*-
"""统一日志（TODO P3）：一次配置，全项目复用

* 控制台输出（便于演示时观察链路：RAG 命中、LLM 降级、PDF 引擎选择、RPA 推送结果）
* 文件输出：`outputs/logs/app.log`，单文件 2MB × 5 份滚动，便于排障与留证
* 级别可用 `LOG_LEVEL`（默认 INFO）与 `LOG_TO_FILE=0` 控制
* 与 uvicorn 的日志共存：只接管本项目 logger（`pharma.*`），不动 uvicorn 配置

用法：
    from .logging_conf import get_logger
    log = get_logger(__name__)      # 自动映射为 pharma.app.xxx
    log.info("...") / log.warning("...") / log.exception("...")
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "outputs", "logs")
_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%H:%M:%S"
_configured = False


def setup(level=None, to_file=None):
    """初始化根 logger（幂等，可重复调用）"""
    global _configured
    root = logging.getLogger("pharma")
    if _configured:
        return root

    lvl = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    root.setLevel(getattr(logging, lvl, logging.INFO))
    root.propagate = False

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    root.addHandler(console)

    want_file = to_file if to_file is not None else os.environ.get("LOG_TO_FILE", "1") != "0"
    if want_file:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            fh = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"), maxBytes=2 * 1024 * 1024,
                                     backupCount=5, encoding="utf-8")
            fh.setFormatter(logging.Formatter(_FMT))
            root.addHandler(fh)
        except Exception as e:      # 只读目录等场景不影响主流程
            root.warning("日志文件不可写（%s），仅输出到控制台", e)

    _configured = True
    root.debug("日志初始化完成：level=%s file=%s", lvl, want_file)
    return root


def get_logger(name="app"):
    """获取项目 logger；`__name__` 形如 app.rag → pharma.app.rag"""
    setup()
    short = name.replace("app.", "").replace("__main__", "main")
    return logging.getLogger(f"pharma.{short}")


def log_path():
    return os.path.join(LOG_DIR, "app.log")
