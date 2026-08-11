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


def artifact_dirs(floodmind_root):
    """Artifact Store 目录（target §15）。content-addressed objects + JSON manifests。

    floodmind_root 即 ArtifactService 的 base_dir（artifact store 根）；
    objects/manifests 直接落在其下。调用方传入 store 根，无需再叠一层 artifacts/。
    """
    base = Path(floodmind_root)
    return {
        "artifact_root": base,
        "objects_dir": base / "objects",
        "manifests_dir": base / "manifests",
    }
