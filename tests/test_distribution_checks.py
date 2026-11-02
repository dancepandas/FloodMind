"""Distribution validation must not accidentally import the checkout."""
from pathlib import Path
import subprocess

import pytest

from scripts.check_distribution import copy_tracked_source, verify_installed_package, check_distribution, PROBE


def test_probe_is_isolated_and_errors_propagate(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.CalledProcessError(7, command)
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        verify_installed_package(Path('venv-python'), tmp_path)
    command, options = calls[0]
    assert command[:3] == ['venv-python', '-I', '-c']
    assert options['cwd'] == tmp_path
    assert options['check'] is True


@pytest.mark.parametrize('missing', ['config/settings_template.json', 'skills/csv/SKILL.md'])
def test_probe_rejects_missing_resources(tmp_path, monkeypatch, missing):
    import importlib.resources
    root = importlib.resources.files('floodmind')
    class Resources:
        def joinpath(self, name):
            if name == missing:
                return tmp_path / 'absent'
            return root.joinpath(name)
    monkeypatch.setattr(importlib.resources, 'files', lambda name: Resources())
    # The installed-location check must remain active; use the actual interpreter prefix.
    import sys
    import floodmind
    monkeypatch.setattr(sys, 'prefix', str(Path(floodmind.__file__).resolve().parents[1]))
    monkeypatch.setattr('importlib.metadata.version', lambda name: floodmind.__version__)
    with pytest.raises(AssertionError, match=missing):
        exec(PROBE, {})


def test_copy_only_tracked_files_and_reject_empty_list(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'pyproject.toml').write_text('controlled')
    (root / 'secret.txt').write_text('not controlled')
    destination = tmp_path / 'source'
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: b'pyproject.toml\0')
    copy_tracked_source(root, destination)
    assert (destination / 'pyproject.toml').read_text() == 'controlled'
    assert not (destination / 'secret.txt').exists()
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: b'')
    with pytest.raises(ValueError):
        copy_tracked_source(root, tmp_path / 'empty')


@pytest.mark.parametrize('platform, executable', [('win32', 'floodmind.exe'), ('linux', 'floodmind')])
def test_probe_runs_installed_console_entry(tmp_path, monkeypatch, platform, executable):
    calls = []
    monkeypatch.setattr('scripts.check_distribution.sys.platform', platform)
    monkeypatch.setattr(subprocess, 'run', lambda command, **options: calls.append((command, options)))
    python = tmp_path / 'environment' / 'bin-or-Scripts' / 'python'
    outside = tmp_path / 'outside'
    verify_installed_package(python, outside)
    assert calls[1][0] == [str(python.parent / executable), '--help']
    assert calls[1][1] == {'cwd': outside, 'check': True}


def test_archive_source_uses_explicit_manifest_without_git(tmp_path, monkeypatch):
    root = tmp_path / 'archive'
    root.mkdir()
    (root / 'pyproject.toml').write_text('controlled')
    (root / 'unlisted.txt').write_text('excluded')
    manifest = tmp_path / 'manifest'
    manifest.write_bytes(b'pyproject.toml\0')
    def no_git(*args, **kwargs):
        pytest.fail('Archive input must not query Git')
    monkeypatch.setattr(subprocess, 'check_output', no_git)
    destination = tmp_path / 'source'
    copy_tracked_source(root, destination, source_manifest=manifest)
    assert (destination / 'pyproject.toml').read_text() == 'controlled'
    assert not (destination / 'unlisted.txt').exists()


def test_build_failure_cleans_temporary_directory(tmp_path, monkeypatch):
    source_dirs = []
    def copy(root, destination):
        destination.mkdir()
        source_dirs.append(destination)
    monkeypatch.setattr('scripts.check_distribution.copy_tracked_source', copy)
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        check_distribution(tmp_path)
    assert source_dirs and not source_dirs[0].exists()
