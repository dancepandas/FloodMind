"""
Artifact snapshot — capture file system state for resume verification.

Uses ArtifactWatcher to detect changes; stores a manifest of files
so we can verify if the file system matches the checkpoint.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


def hash_file(path: str) -> str:
    """Return MD5 hash of file content (fast, not cryptographic)."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def take_snapshot(output_dir: str, upload_dir: str = "") -> str:
    """Capture a snapshot of the session's output directory.

    Returns a JSON string with file paths and hashes.
    """
    manifest: Dict[str, str] = {}

    for base_dir in (output_dir, upload_dir):
        if not base_dir or not os.path.isdir(base_dir):
            continue
        for root, _, files in os.walk(base_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, base_dir)
                manifest[relpath] = hash_file(fpath)

    return json.dumps(manifest, ensure_ascii=False, sort_keys=True)


def verify_snapshot(output_dir: str, snapshot_json: str) -> bool:
    """Verify if current file state matches the snapshot."""
    try:
        expected = json.loads(snapshot_json)
    except Exception:
        return False

    current: Dict[str, str] = {}
    if output_dir and os.path.isdir(output_dir):
        for root, _, files in os.walk(output_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, output_dir)
                current[relpath] = hash_file(fpath)

    return current == expected
