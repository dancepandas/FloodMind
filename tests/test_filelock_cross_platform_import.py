"""跨平台导入兼容测试：filelock 模块在 POSIX 平台必须可导入。

msvcrt 是 Windows 专属标准库，Linux/macOS 上不存在。
顶部无条件 `import msvcrt` 会让 `from floodmind import Agent` 在
非 Windows 平台直接 ModuleNotFoundError。
"""

import builtins
import importlib
import os
import sys


def test_filelock_imports_without_msvcrt(monkeypatch):
    """模拟 POSIX 环境（os.name='posix' 且无 msvcrt 模块），filelock 必须可导入。"""
    # 清掉缓存模块，强制重新执行模块体
    for name in [n for n in sys.modules if n == "floodmind.common.filelock"]:
        monkeypatch.delitem(sys.modules, name)
    # 模拟 POSIX：无 msvcrt 模块，os.name == 'posix'
    for name in [n for n in sys.modules if n == "msvcrt" or n.startswith("msvcrt.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(os, "name", "posix")

    real_import = builtins.__import__

    def posix_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "msvcrt":
            raise ModuleNotFoundError("No module named 'msvcrt' (simulated POSIX)")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", posix_import)

    filelock = importlib.import_module("floodmind.common.filelock")

    assert hasattr(filelock, "FileLock")
    assert hasattr(filelock, "FileLockTimeoutError")
    lock = filelock.FileLock("x.lock")
    assert lock.timeout == 10.0
