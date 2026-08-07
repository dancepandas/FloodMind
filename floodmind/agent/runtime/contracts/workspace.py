"""
Runtime Contracts — Workspace（工作区抽象）

把"Agent 在哪里工作"从散落的 cwd/output_dir/data/sessions 中抽出来。

设计原则：
- web_session：保持网页版旧布局，user_dir = data/sessions/<sid>/outputs。
- folder_first：用户启动/打开的目录就是 primary_dir/cwd，FloodMind 内部状态收纳到
  primary_dir/.floodmind/。
- user_dir 保持向后兼容：旧语义是主代理产物目录；folder_first 中等于 primary_dir。
- session_root：app-data 根，memory/checkpoint/uploads/journal/trace 落盘处。
- sandbox_base：子代理沙盒根。
- readable_roots / writable_roots：授权的额外外部根，不用于替代 primary_dir。

该模块为纯数据契约，不依赖任何业务实现，可被 path_service / sandbox_service / settings 安全 import。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class Workspace:
    """agent 运行期工作区。

    所有路径字段为绝对路径（resolved）。frozen=True 保证运行期不可变，
    避免 path_service / sandbox_service 持有后被意外篡改。
    """

    # 兼容字段：主代理可写目录。web_session=session outputs；folder_first=用户工作区根。
    user_dir: Path
    # app-data 根：memory/checkpoint/uploads/journal/trace 落盘处。
    session_root: Path
    # 子代理沙盒根：所有 sub-workspace 创建在此下。
    sandbox_base: Path
    # 写白名单追加根（除 primary/user/sandbox 外额外允许写入的目录）
    writable_roots: Tuple[Path, ...] = field(default_factory=tuple)
    # 读白名单追加根
    readable_roots: Tuple[Path, ...] = field(default_factory=tuple)
    # 覆盖保护开关：True=禁止覆盖已存在文件；False=允许（默认）
    overwrite_protection: bool = False

    # ── Harness / folder-first 语义字段（新增，均保持可选兼容） ─────────────
    mode: str = "web_session"
    # primary_dir：用户启动/打开的工作区根。未提供时回退 user_dir。
    primary_dir: Optional[Path] = None
    # cwd：工具默认执行目录。未提供时回退 primary_dir/user_dir。
    cwd: Optional[Path] = None
    # state_dir：FloodMind 私有状态根。folder_first 下为 primary_dir/.floodmind。
    state_dir: Optional[Path] = None
    # artifact_dir/tmp_dir/scripts_dir：session scoped 的归档产物、临时文件、脚本目录。
    artifact_dir: Optional[Path] = None
    tmp_dir: Optional[Path] = None
    scripts_dir: Optional[Path] = None

    @classmethod
    def from_cwd(cls, session_id: str = "sdk-agent", **kwargs) -> "Workspace":
        """SDK 便捷构造：当前进程 cwd 即 folder-first 工作区。"""
        return cls.from_folder(Path.cwd(), session_id=session_id, **kwargs)

    @classmethod
    def from_folder(
        cls,
        folder: Path | str,
        *,
        session_id: str = "sdk-agent",
        writable_roots: Tuple[Path | str, ...] = (),
        readable_roots: Tuple[Path | str, ...] = (),
        overwrite_protection: bool = False,
    ) -> "Workspace":
        """SDK 便捷构造：显式目录作为 folder-first 工作区。"""
        root = Path(folder).resolve()
        state_dir = root / ".floodmind"
        return cls(
            user_dir=root,
            session_root=state_dir / "sessions",
            sandbox_base=state_dir / "sandboxes",
            writable_roots=tuple(Path(p).resolve() for p in writable_roots),
            readable_roots=tuple(Path(p).resolve() for p in readable_roots),
            overwrite_protection=overwrite_protection,
            mode="folder_first",
            primary_dir=root,
            cwd=root,
            state_dir=state_dir,
            artifact_dir=state_dir / "artifacts" / session_id,
            tmp_dir=state_dir / "tmp" / session_id,
            scripts_dir=state_dir / "scripts" / session_id,
        )

    @property
    def default_cwd(self) -> Path:
        """工具默认 cwd。folder_first = cwd/primary_dir；旧模式 = user_dir。"""
        return self.cwd or self.primary_dir or self.user_dir

    @property
    def workspace_dir(self) -> Path:
        """当前 primary workspace 根。"""
        return self.primary_dir or self.user_dir

    @property
    def is_folder_first(self) -> bool:
        return self.mode == "folder_first"

    def add_writable_root(self, *paths) -> "Workspace":
        """运行时追加写白名单根（幂等，宿主显式调用的变更 API）。

        宿主可据此放行 workspace 外目录（如 web 的 uploads/、web_workspace/），
        exec_bash/file 工具的写路径检查即刻生效（PathService 持活引用）。
        frozen=True 保护的是内部服务对字段的意外篡改；本方法是宿主显式授权，用
        object.__setattr__ 绕过。
        """
        new = [Path(p).resolve() for p in paths]
        merged = list(self.writable_roots)
        for p in new:
            if p not in merged:
                merged.append(p)
        object.__setattr__(self, "writable_roots", tuple(merged))
        return self

    def add_readable_root(self, *paths) -> "Workspace":
        """运行时追加读白名单根（幂等，宿主显式调用的变更 API）。"""
        new = [Path(p).resolve() for p in paths]
        merged = list(self.readable_roots)
        for p in new:
            if p not in merged:
                merged.append(p)
        object.__setattr__(self, "readable_roots", tuple(merged))
        return self

    def ensure(self) -> "Workspace":
        """建工作区相关根目录。幂等。"""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.sandbox_base.mkdir(parents=True, exist_ok=True)
        for p in (self.state_dir, self.artifact_dir, self.tmp_dir, self.scripts_dir):
            if p is not None:
                p.mkdir(parents=True, exist_ok=True)
        return self
