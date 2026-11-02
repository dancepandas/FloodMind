"""Profile 测试退出后必须恢复调用方进程配置。"""
import importlib
import importlib.util
import os
from pathlib import Path

import pytest


def test_profile_fixture_restores_callers_environment_and_settings(tmp_path, monkeypatch):
    settings = importlib.import_module("floodmind.config.settings")
    spec = importlib.util.spec_from_file_location(
        "profile_isolation_subject", Path(__file__).with_name("test_profile_system.py"),
    )
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)

    caller_home = tmp_path / "caller"
    caller_home.mkdir()
    caller_profiles = caller_home / "profiles"
    caller_config = {"sentinel": "caller"}
    monkeypatch.setenv("FLOODMIND_HOME", str(caller_home))
    monkeypatch.setenv("FLOODMIND_PROFILE", "caller-profile")
    monkeypatch.setattr(settings, "_DEFAULT_HOME", caller_home)
    monkeypatch.setattr(settings, "_PROFILES_ROOT", caller_profiles)
    monkeypatch.setattr(settings, "_active_profile_cache", "caller-profile")
    monkeypatch.setattr(settings, "_config_cache", caller_config)
    monkeypatch.chdir(caller_home)

    isolated = tmp_path / "isolated"
    isolated.mkdir()
    with pytest.MonkeyPatch.context() as scoped:
        home = subject.isolated_profile.__wrapped__(isolated, scoped)
        subject.test_2_soul()
        subject.test_5_config_paths(isolated, scoped)
        subject.test_7_custom_soul(isolated, scoped)
        assert settings.get_floodmind_home() == home
        assert settings.get_active_profile() == "default"
        assert Path.cwd() == isolated
        assert not list(caller_home.iterdir())

    assert os.environ["FLOODMIND_HOME"] == str(caller_home)
    assert os.environ["FLOODMIND_PROFILE"] == "caller-profile"
    assert settings._DEFAULT_HOME == caller_home
    assert settings._PROFILES_ROOT == caller_profiles
    assert settings._active_profile_cache == "caller-profile"
    assert settings._config_cache is caller_config
    assert Path.cwd() == caller_home
    assert not list(caller_home.iterdir())
