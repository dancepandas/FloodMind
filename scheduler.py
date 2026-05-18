"""Background scheduler for FloodMind scheduled Agent tasks."""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Set

from dotenv import load_dotenv

load_dotenv()

os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
os.environ.setdefault('HF_HUB_OFFLINE', '1')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.native import create_flood_agent
from agent.scheduled_task_runtime import ScheduledTaskRuntime
from config.settings import settings
from memory import DualMemory, SessionManager
from models import get_qwen_llm_service, create_llm_service_from_preset
from config.model_presets import get_default_model_key
from tools import set_rag_config, set_session_context


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
LOGS_DIR = PROJECT_ROOT / "logs"


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    from logging.handlers import TimedRotatingFileHandler

    file_handler = TimedRotatingFileHandler(
        LOGS_DIR / "scheduler.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


logger = logging.getLogger(__name__)


def build_session_manager() -> SessionManager:
    return SessionManager({
        "max_active_sessions": int(os.environ.get("MAX_SESSIONS", 10)),
        "idle_timeout_minutes": int(os.environ.get("IDLE_TIMEOUT", 30)),
        "session_retention_days": int(os.environ.get("SESSION_RETENTION", 30)),
        "upload_retention_days": int(os.environ.get("UPLOAD_RETENTION", 7)),
        "output_retention_days": int(os.environ.get("OUTPUT_RETENTION", 30)),
        "cleanup_interval_minutes": int(os.environ.get("CLEANUP_INTERVAL", 60)),
        "data_dir": str(DATA_DIR),
    })


def create_agent_for_session(session_manager: SessionManager, session_id: str):
    set_session_context(session_id=session_id, output_dir=str(session_manager.get_output_dir(session_id)))
    set_rag_config(
        enabled=settings.rag.enabled,
        persist_dir=settings.rag.persist_dir,
        embedding_model=settings.rag.embedding_model,
        top_k=settings.rag.top_k,
        session_id=session_id,
    )

    model_key = get_default_model_key()
    llm_service = create_llm_service_from_preset(
        model_key,
        enable_reasoning=settings.qwen.enable_reasoning,
    )
    memory = DualMemory(
        session_id=session_id,
        max_short_term=settings.agent.max_history,
        context_window=settings.agent.context_window,
        persist_dir=session_manager.get_memory_dir(session_id),
    )
    return create_flood_agent(llm_service=llm_service, memory=memory, session_id=session_id, enable_search=False)


def get_or_create_agent(session_manager: SessionManager, session_id: str):
    session_manager.touch_session(session_id)
    agent = session_manager.get_agent(session_id)
    if agent:
        set_session_context(session_id=session_id, output_dir=str(session_manager.get_output_dir(session_id)))
        return agent
    _, agent = session_manager.get_or_create_session(
        session_id,
        agent_factory=lambda sid: create_agent_for_session(session_manager, sid),
    )
    return agent


def snapshot_outputs(output_dir: Path) -> Set[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: Set[str] = set()
    for path in output_dir.rglob("*"):
        if path.is_file():
            files.add(str(path.resolve()))
    return files


def execute_task(runtime: ScheduledTaskRuntime, session_manager: SessionManager, task: Dict[str, str]) -> None:
    task_id = str(task.get("id") or "")
    session_id = str(task.get("session_id") or "default")
    command = str(task.get("command") or "").strip()
    output_dir = Path(session_manager.get_output_dir(session_id))
    before = snapshot_outputs(output_dir)

    logger.info("开始执行定时任务: id=%s session=%s", task_id, session_id)
    try:
        agent = get_or_create_agent(session_manager, session_id)
        set_session_context(session_id=session_id, output_dir=str(output_dir))
        result = agent.run(command)
        after = snapshot_outputs(output_dir)
        new_files = [Path(path) for path in sorted(after - before)]
        artifacts = runtime.build_artifact_records(session_id, new_files, base_dir=output_dir)
        runtime.complete_task(task_id, success=True, result=result, artifacts=artifacts)
        logger.info("定时任务执行完成: id=%s artifacts=%s", task_id, len(artifacts))
    except Exception as exc:
        logger.error("定时任务执行失败: id=%s error=%s", task_id, exc, exc_info=True)
        after = snapshot_outputs(output_dir)
        new_files = [Path(path) for path in sorted(after - before)]
        artifacts = runtime.build_artifact_records(session_id, new_files, base_dir=output_dir)
        runtime.complete_task(task_id, success=False, error=str(exc), artifacts=artifacts)


def seconds_until_next_hour() -> float:
    now = datetime.now()
    next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    return max(1.0, (next_hour - now).total_seconds())


def run_once(runtime: ScheduledTaskRuntime, session_manager: SessionManager, lookback_minutes: int, lookahead_minutes: int, limit: int) -> int:
    tasks = runtime.claim_due_tasks(lookback_minutes=lookback_minutes, lookahead_minutes=lookahead_minutes, limit=limit)
    if not tasks:
        logger.info("心跳完成：无到期任务")
        return 0
    for task in tasks:
        execute_task(runtime, session_manager, task)
    session_manager.save_all()
    return len(tasks)


def main() -> int:
    parser = argparse.ArgumentParser(description="FloodMind scheduled task scheduler")
    parser.add_argument("--once", action="store_true", help="只执行一次心跳扫描")
    parser.add_argument("--lookback-minutes", type=int, default=int(os.environ.get("SCHEDULER_LOOKBACK_MINUTES", 60)))
    parser.add_argument("--lookahead-minutes", type=int, default=int(os.environ.get("SCHEDULER_LOOKAHEAD_MINUTES", 60)))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("SCHEDULER_MAX_TASKS_PER_HEARTBEAT", 1)))
    args = parser.parse_args()

    setup_logging()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "sessions").mkdir(parents=True, exist_ok=True)

    runtime = ScheduledTaskRuntime()
    session_manager = build_session_manager()
    logger.info("FloodMind Scheduler 已启动，任务文件: %s", runtime.storage_path)

    try:
        if args.once:
            run_once(runtime, session_manager, args.lookback_minutes, args.lookahead_minutes, args.limit)
            return 0
        run_once(runtime, session_manager, args.lookback_minutes, args.lookahead_minutes, args.limit)
        while True:
            sleep_seconds = seconds_until_next_hour()
            logger.info("距离下一次整点心跳 %.0f 秒", sleep_seconds)
            time.sleep(sleep_seconds)
            run_once(runtime, session_manager, args.lookback_minutes, args.lookahead_minutes, args.limit)
    except KeyboardInterrupt:
        logger.info("Scheduler 收到退出信号")
    finally:
        session_manager.save_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
