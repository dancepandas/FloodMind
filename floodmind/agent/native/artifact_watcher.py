"""
Native Agent Runtime - ArtifactWatcher

检测本轮输出目录中的新产物文件，与现有 web_server.py 产物事件格式对齐。
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from floodmind.agent.native.types import ArtifactRecord

logger = logging.getLogger(__name__)

_ARTIFACT_EXTENSIONS: Set[str] = {
    ".json", ".csv", ".xlsx", ".xls", ".docx", ".pdf", ".md", ".txt",
    ".png", ".jpg", ".jpeg", ".webp",
}

_IMAGE_EXTENSIONS: Set[str] = {".png", ".jpg", ".jpeg", ".webp"}


class ArtifactWatcher:
    def __init__(self, output_dir: str, upload_dir: str = ""):
        self._output_dir = output_dir
        self._upload_dir = upload_dir
        self._snapshot: Set[str] = set()
        self._snapshot_time: float = 0.0

    def take_snapshot(self) -> None:
        self._snapshot = self._list_output_files()
        self._snapshot_time = self._now()

    def detect_new_artifacts(self) -> List[ArtifactRecord]:
        current_files = self._list_output_files()
        new_files = current_files - self._snapshot
        records = []
        for file_path_str in sorted(new_files):
            file_path = Path(file_path_str)
            if not file_path.exists():
                continue
            if self._is_in_upload_dir(file_path_str):
                continue
            ext = file_path.suffix.lower()
            if ext not in _ARTIFACT_EXTENSIONS:
                continue
            try:
                mtime = os.path.getmtime(file_path_str)
            except OSError:
                continue
            if mtime < self._snapshot_time - 1:
                continue
            kind = "image" if ext in _IMAGE_EXTENSIONS else "file"
            try:
                relative_path = str(Path(file_path_str).relative_to(self._output_dir))
            except ValueError:
                relative_path = file_path.name
            records.append(ArtifactRecord(
                file_name=relative_path,
                file_path=file_path_str,
                kind=kind,
                source_tool="",
                verified=True,
                metadata={
                    "size": file_path.stat().st_size,
                    "mtime": datetime.fromtimestamp(mtime).isoformat(),
                },
            ))
        return records

    def verify_artifact_exists(self, file_name: str) -> Optional[ArtifactRecord]:
        if not self._output_dir:
            return None
        target = Path(self._output_dir) / file_name
        if not target.exists() or not target.is_file():
            return None
        ext = target.suffix.lower()
        if ext not in _ARTIFACT_EXTENSIONS:
            return None
        kind = "image" if ext in _IMAGE_EXTENSIONS else "file"
        return ArtifactRecord(
            file_name=target.name,
            file_path=str(target),
            kind=kind,
            source_tool="",
            verified=True,
        )

    def _list_output_files(self) -> Set[str]:
        if not self._output_dir or not os.path.isdir(self._output_dir):
            return set()
        result = set()
        try:
            for dirpath, _dirnames, filenames in os.walk(self._output_dir):
                for filename in filenames:
                    full_path = os.path.join(dirpath, filename)
                    if os.path.isfile(full_path):
                        result.add(full_path)
        except OSError:
            pass
        return result

    def _is_in_upload_dir(self, file_path_str: str) -> bool:
        if not self._upload_dir:
            return False
        try:
            Path(file_path_str).resolve().relative_to(Path(self._upload_dir).resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _now() -> float:
        return datetime.now().timestamp()
