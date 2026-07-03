"""Tests for artifact validation logic."""

import tempfile
from pathlib import Path

import pytest

from floodmind.agent.native.native_flood_agent import NativeFloodAgent


class TestFindMissingArtifacts:
    def test_absolute_existing_file(self):
        tmp = tempfile.mkdtemp()
        f = Path(tmp) / "report.md"
        f.write_text("x")
        missing = NativeFloodAgent._find_missing_artifacts([str(f)], tmp)
        assert missing == []

    def test_absolute_missing_file(self):
        tmp = tempfile.mkdtemp()
        missing = NativeFloodAgent._find_missing_artifacts([str(Path(tmp) / "report.md")], tmp)
        assert missing == [str(Path(tmp) / "report.md")]

    def test_relative_existing_file(self):
        tmp = tempfile.mkdtemp()
        f = Path(tmp) / "report.md"
        f.write_text("x")
        missing = NativeFloodAgent._find_missing_artifacts(["report.md"], tmp)
        assert missing == []

    def test_relative_missing_file(self):
        tmp = tempfile.mkdtemp()
        missing = NativeFloodAgent._find_missing_artifacts(["report.md"], tmp)
        assert missing == ["report.md"]

    def test_mixed_paths(self):
        tmp = tempfile.mkdtemp()
        f = Path(tmp) / "ok.csv"
        f.write_text("x")
        missing = NativeFloodAgent._find_missing_artifacts(["ok.csv", "missing.xlsx"], tmp)
        assert missing == ["missing.xlsx"]

    def test_empty_and_none_ignored(self):
        tmp = tempfile.mkdtemp()
        missing = NativeFloodAgent._find_missing_artifacts(["", None], tmp)
        assert missing == []
