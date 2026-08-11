"""进程身份对账（§12 / §16.4 step5）。psutil 提供跨平台 PID 存活与身份匹配。"""

from __future__ import annotations

import logging
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


def process_exists(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception as e:
        logger.warning("pid_exists(%s) failed: %s", pid, e)
        return False


def process_create_time(pid: int) -> Optional[float]:
    """进程创建时间（epoch 秒）。不存在/异常返回 None。"""
    if pid is None or pid <= 0:
        return None
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None
    except Exception as e:
        logger.warning("process_create_time(%s) failed: %s", pid, e)
        return None


def pid_identity_matches(pid: int, stored_create_time: Optional[float]) -> bool:
    """PID 存活且创建时间匹配（防 PID 复用误判）。"""
    if not process_exists(pid):
        return False
    actual = process_create_time(pid)
    if actual is None:
        return False
    if stored_create_time is None:
        return True  # 未存 create_time：仅按存活判定
    return abs(actual - stored_create_time) < 2.0
