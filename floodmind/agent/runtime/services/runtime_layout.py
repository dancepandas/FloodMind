"""Run 内部目录布局（目标 §18）。"""

from pathlib import Path


def thread_dirs(runtime_dir: Path, conversation_id: str, task_id: str,
                run_id: str, thread_id: str) -> dict:
    base = (Path(runtime_dir) / "conversations" / conversation_id / "tasks" / task_id
            / "runs" / run_id / "threads" / thread_id)
    return {
        "thread_dir": base,
        "state_dir": base / "state",
        "tmp_dir": base / "tmp",
        "scripts_dir": base / "scripts",
    }


def lease_file(runtime_dir: Path, conversation_id: str, task_id: str,
               run_id: str) -> Path:
    """Resume fencing lease 文件路径（目标 §16.4）。"""
    base = (Path(runtime_dir) / "conversations" / conversation_id / "tasks" / task_id
            / "runs" / run_id)
    return base / "lease.json"
