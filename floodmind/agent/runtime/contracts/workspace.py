"""
Runtime Contracts — Workspace（工作区抽象）

把"产物写哪"这一个横切面从写死的 data/sessions/<id>/outputs/ 抽出来。

设计原则：
- user_dir：主代理可写产物目录。网页版 = session outputs；桌面版 = 用户打开的文件夹。
- session_root：app-data 根，memory/checkpoint/uploads 落盘处（横切，逻辑不动，两版只挪位置）。
- sandbox_base：子代理沙盒根。网页版 = session_root（保持旧布局）；桌面版 = user_dir/.floodmind/sandboxes。
- 隔离靠目录分配（delegate_cwd），不靠权限锁——见 PathService 子代理写范围。

该模块为纯数据契约，不依赖任何业务实现，可被 path_service / sandbox_service / settings 安全 import。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class Workspace:
    """agent 运行期工作区。

    所有字段为绝对路径（resolved）。frozen=True 保证运行期不可变，
    避免 path_service / sandbox_service 持有后被意外篡改。
    """

    # 主代理产物目录：相对路径写入默认落此（网页版=session outputs，桌面版=用户文件夹）
    user_dir: Path
    # app-data 根：memory/checkpoint/uploads 落盘处（两版不同，逻辑不动）
    session_root: Path
    # 子代理沙盒根：所有 sub-workspace 创建在此下
    # 网页版 = session_root（保持 data/sessions/<sub>/workspace 旧布局）
    # 桌面版 = user_dir / ".floodmind" / "sandboxes"（落在用户文件夹内）
    sandbox_base: Path
    # 写白名单追加根（除 user_dir / sandbox_base 外额外允许写入的目录）
    writable_roots: Tuple[Path, ...] = field(default_factory=tuple)
    # 读白名单追加根
    readable_roots: Tuple[Path, ...] = field(default_factory=tuple)
    # 覆盖保护开关：True=禁止覆盖已存在文件；False=允许（默认）
    overwrite_protection: bool = False

    @property
    def default_cwd(self) -> Path:
        """主代理 Bash 默认 cwd = user_dir；子代理由 SandboxContext/delegate_cwd 覆盖。"""
        return self.user_dir

    def ensure(self) -> "Workspace":
        """建三个根目录（user_dir / session_root / sandbox_base）。幂等。"""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.sandbox_base.mkdir(parents=True, exist_ok=True)
        return self