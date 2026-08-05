"""
FloodMind CLI

用法:
  floodmind                           交互菜单 (Run/Chat/legacy 提示)
  floodmind tui                       Legacy TUI 迁移提示
  floodmind web                       Legacy Web 迁移提示
  floodmind serve                     Legacy Web 服务迁移提示
  floodmind chat                      纯文本终端对话
  floodmind run "任务描述"             单次任务执行
  floodmind init                      初始化配置
  floodmind skill create <name>       从模板创建 Skill
  floodmind skill list                列出已安装 Skill
  floodmind config show               显示当前配置
  floodmind config set <key> <val>    设置配置项
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import click


# ── 日志 ────────────────────────────────────────────────────

def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _validate_api_key() -> None:
    """校验 API Key 是否已配置，未配置则友好提示并退出。"""
    from floodmind.config.settings import settings, get_config

    api_key = settings.model.api_key
    if api_key and api_key.strip():
        return

    provider = settings.model.provider_name
    cfg = get_config()
    provider_section = cfg.get("provider", cfg.get("providers", {}))
    provider_data = provider_section.get(provider, {}) if isinstance(provider_section, dict) else {}
    options = provider_data.get("options", provider_data) if isinstance(provider_data, dict) else {}
    base_url = options.get("baseURL", options.get("base_url", ""))

    dashscope_fallback = "  或  DASHSCOPE_API_KEY" if provider == "dashscope" else ""

    click.echo(f"""
  FloodMind v1.1.0

  [!] 未配置 API Key

  当前 Provider: {provider}
  接口地址: {base_url}

  请通过以下任一方式配置 API Key:

  1. 环境变量 (临时):
     set FLOODMIND_API_KEY=你的key{dashscope_fallback}

  2. 用户级配置 (推荐，一劳永逸):
      floodmind config set providers.{provider}.api_key 你的key

   3. 直接编辑配置文件:
       ~/.floodmind/settings.json
      在 providers.{provider}.api_key 处填写你的key

  配置完成后重新运行 floodmind 即可。
""", err=True)
    raise SystemExit(1)


# ── 公共入口函数 ─────────────────────────────────────────────

_BANNER = r"""
███████╗ ██╗       ██████╗   ██████╗  ██████╗  ███╗   ███╗ ██╗ ███╗   ██╗ ██████╗
██╔════╝ ██║      ██╔═══██╗ ██╔═══██╗ ██╔══██╗ ████╗ ████║ ██║ ████╗  ██║ ██╔══██╗
█████╗   ██║      ██║   ██║ ██║   ██║ ██║  ██║ ██╔████╔██║ ██║ ██╔██╗ ██║ ██║  ██║
██╔══╝   ██║      ██║   ██║ ██║   ██║ ██║  ██║ ██║╚██╔╝██║ ██║ ██║╚██╗██║ ██║  ██║
██║      ███████╗ ╚██████╔╝ ╚██████╔╝ ██████╔╝ ██║ ╚═╝ ██║ ██║ ██║ ╚████║ ██████╔╝
╚═╝      ╚══════╝  ╚═════╝   ╚═════╝  ╚═════╝  ╚═╝     ╚═╝ ╚═╝ ╚═╝  ╚═══╝ ╚═════╝
"""

_BANNER_SUB = "基于大语言模型的智能洪水预报系统  |  v1.1.0"


@click.group(invoke_without_command=True)
@click.option("--tui", is_flag=True, help="显示 TUI legacy 迁移提示")
@click.option("--web", "web_mode", is_flag=True, help="显示 Web legacy 迁移提示")
@click.option("--port", default=13014, type=int, help="Legacy Web 端口参数（仅兼容提示）")
@click.option("--host", default="0.0.0.0", help="Legacy Web 监听地址参数（仅兼容提示）")
@click.option("--model", "-m", help="模型名称 (provider:model)", hidden=True)
@click.option("--reasoning/--no-reasoning", default=None, help="启用推理模式", hidden=True)
@click.option("--verbose", "-v", is_flag=True, help="显示详细日志", hidden=True)
@click.version_option(version="1.1.0", prog_name="floodmind")
@click.pass_context
def main(ctx, tui, web_mode, port, host, model, reasoning, verbose):
    """FloodMind — 智能洪水预报 Agent 系统

    无参数时弹出交互菜单；--tui / --web 仅输出 legacy 迁移提示。
    """
    if ctx.invoked_subcommand is not None:
        return

    _setup_logging(verbose=verbose)
    os.environ.setdefault("DASHSCOPE_API_KEY", os.getenv("FLOODMIND_API_KEY", ""))

    if tui:
        return _legacy_ui_notice("tui")

    if web_mode:
        return _legacy_ui_notice("web")

    # 无参数：显示交互菜单
    _validate_api_key()
    from floodmind.cli_interactive import run_menu
    raise SystemExit(run_menu(model=model, port=port, host=host))


# ── tui ─────────────────────────────────────────────────────

@main.command()
@click.option("--model", "-m", default="", help="模型名称")
@click.option("--port", default=13014, type=int, help="Legacy Web 端口参数（仅兼容提示）")
@click.option("--host", default="localhost", help="Legacy Web 监听地址参数（仅兼容提示）")
def tui(model, port, host):
    """Legacy TUI 入口（已迁移到 SDK-first 路线）"""
    _setup_logging()
    raise SystemExit(_legacy_ui_notice("tui"))


# ── web ─────────────────────────────────────────────────────

@main.command("web")
@click.option("--host", default="0.0.0.0", help="Legacy Web 监听地址参数（仅兼容提示）")
@click.option("--port", default=13014, type=int, help="Legacy Web 端口参数（仅兼容提示）")
@click.option("--no-browser", is_flag=True, help="Legacy 兼容参数（仅兼容提示）")
@click.option("--no-scheduler", is_flag=True, help="Legacy 兼容参数（仅兼容提示）")
def web_cmd(host, port, no_browser, no_scheduler):
    """Legacy Web 入口（已迁移到 SDK-first 路线）"""
    _setup_logging()
    raise SystemExit(_legacy_ui_notice("web"))


# ── chat (纯文本) ───────────────────────────────────────────

@main.command()
@click.option("--model", "-m", help="模型名称 (provider:model)")
@click.option("--reasoning/--no-reasoning", default=None, help="启用推理模式")
@click.option("--tui", "use_tui", is_flag=True, help="改用 TUI 界面")
@click.option("--web", "use_web", is_flag=True, help="改用 Web 界面")
@click.option("--verbose", "-v", is_flag=True, help="显示详细日志")
def chat(model, reasoning, use_tui, use_web, verbose):
    """启动交互式对话（纯文本终端模式）"""
    _setup_logging(verbose=verbose)
    os.environ.setdefault("DASHSCOPE_API_KEY", os.getenv("FLOODMIND_API_KEY", ""))

    if use_tui:
        return _legacy_ui_notice("tui")
    if use_web:
        return _legacy_ui_notice("web")

    _validate_api_key()
    return _run_chat_legacy(model=model, reasoning=reasoning)


# ── run ─────────────────────────────────────────────────────

@main.command()
@click.argument("task")
@click.option("--model", "-m", help="模型名称")
@click.option("--resume", "resume_session_id", help="从指定 session 的 checkpoint 恢复")
@click.option("--checkpoint", "resume_checkpoint_id", help="指定 checkpoint ID（配合 --resume）")
@click.option("--verbose", "-v", is_flag=True, help="显示详细日志")
def run(task, model, resume_session_id, resume_checkpoint_id, verbose):
    """执行单次任务，支持从 checkpoint 恢复"""
    _setup_logging(verbose=verbose)
    os.environ.setdefault("DASHSCOPE_API_KEY", os.getenv("FLOODMIND_API_KEY", ""))
    _validate_api_key()

    from floodmind.config.settings import settings
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.memory import DualMemory
    from floodmind.agent import create_flood_agent

    if model:
        settings.model.model_name = model

    llm = ModelClient.from_settings(
        temperature=settings.model.temperature,
        max_tokens=settings.model.max_tokens,
    )
    import uuid
    sid = resume_session_id or f"cli-run-{uuid.uuid4().hex[:8]}"
    memory = DualMemory(
        session_id=sid,
        context_window=settings.model.context_window,
        llm=llm,
    )
    workspace = _build_cli_workspace(sid)
    agent = create_flood_agent(llm_service=llm, memory=memory, session_id=sid, workspace=workspace)

    result = agent.run_with_resume(
        task,
        resume_session_id=resume_session_id,
        resume_checkpoint_id=resume_checkpoint_id,
    )
    print(result)


@main.command("list-checkpoints")
@click.argument("session_id")
def list_checkpoints(session_id):
    """列出某 session 的所有 checkpoint"""
    _setup_logging()
    from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
    svc = CheckpointService()
    summaries = svc.list(session_id)
    if not summaries:
        click.echo(f"会话 {session_id} 没有 checkpoint")
        return
    click.echo(f"会话 {session_id} 的 checkpoint ({len(summaries)} 个):")
    for s in summaries:
        click.echo(
            f"  {s.checkpoint_id} | status={s.status} | iteration={s.iteration} | "
            f"time={s.created_at.isoformat()} | files_snapshot={s.has_files_snapshot}"
        )


@main.command("pause")
@click.argument("session_id")
def pause_session(session_id):
    """暂停指定 session 的执行"""
    _setup_logging()
    from floodmind.agent import create_flood_agent
    from floodmind.agent.runtime.services.checkpoint_service import CheckpointService
    from floodmind.agent.native.types import AgentLoopState

    # 尝试通过 agent.pause 暂停当前运行
    agent = create_flood_agent(session_id=session_id)
    if agent.pause(session_id):
        click.echo(f"已请求暂停 session {session_id}，将在下一个状态边界生效")
        return

    # 未在运行：直接修改最新 checkpoint
    svc = CheckpointService()
    try:
        state = svc.load(session_id, state_class=AgentLoopState)
        if state.status not in {"completed", "failed"}:
            state.status = "paused"
            svc.save(state)
            click.echo(f"已暂停 session {session_id} 的最新 checkpoint")
            return
    except Exception as e:
        click.echo(f"暂停失败: {e}")


# ── serve ───────────────────────────────────────────────────

@main.command()
@click.option("--host", default="0.0.0.0", help="Legacy Web 监听地址参数（仅兼容提示）")
@click.option("--port", default=13014, type=int, help="Legacy Web 端口参数（仅兼容提示）")
@click.option("--no-scheduler", is_flag=True, help="Legacy 兼容参数（仅兼容提示）")
def serve(host, port, no_scheduler):
    """Legacy Web 服务入口（已迁移到 SDK-first 路线）"""
    _setup_logging()
    return _legacy_ui_notice("serve")


# ── init ────────────────────────────────────────────────────

_SKILL_TEMPLATE = """---
name: {name}
description: "TRIGGER when: [触发条件]. DO NOT TRIGGER when: [不触发条件]."
version: 1.0
---

# {name}

## 使用场景
- ...

## 执行步骤
1. ...
2. ...

## 注意事项
- ...
"""


@main.command()
@click.option("--dir", "-d", default=".", help="目标目录")
def init(dir):
    """在当前目录初始化 FloodMind 配置"""
    from floodmind.config.settings import _config_path, _load_json_config, _template_path, get_floodmind_home, save_config

    target = Path(dir).resolve()

    config_dir = get_floodmind_home()
    config_path = _config_path()
    if not config_path.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
        template_cfg = _load_json_config(_template_path()) or {}
        save_config(template_cfg)
        click.echo(f"[OK] 配置已创建: {config_path}")
    else:
        click.echo(f"  配置已存在: {config_path}")

    skills_dir = target / "skills"
    skills_dir.mkdir(exist_ok=True)
    click.echo(f"[OK] Skill 目录: {skills_dir}")

    click.echo(f"\nFloodMind 项目已初始化: {target}")
    click.echo(f"下一步: floodmind config set provider.dashscope.options.apiKey <你的key>")
    click.echo(f"配置文件: {config_path}")


# ── skill ───────────────────────────────────────────────────

@main.group()
def skill():
    """Skill 管理"""
    pass


@skill.command()
@click.argument("name")
@click.option("--dir", "-d", default=None, help="目标 skills 目录 (默认 ./skills/)")
def create(name, dir):
    """从模板创建新 Skill"""
    target = Path(dir) if dir else Path.cwd() / "skills"
    target.mkdir(parents=True, exist_ok=True)
    skill_dir = target / name

    if skill_dir.exists():
        click.echo(f"错误: Skill '{name}' 已存在: {skill_dir}")
        return

    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_TEMPLATE.format(name=name), encoding="utf-8")
    click.echo(f"[OK] Skill 已创建: {skill_dir}/")
    click.echo(f"  编辑 {skill_dir}/SKILL.md 填写使用说明")


@skill.command()
@click.option("--dir", "-d", default=None, help="skills 目录")
def list(dir):
    """列出已安装的 Skill"""
    target = Path(dir) if dir else Path.cwd() / "skills"
    if not target.exists():
        click.echo("(无 skills 目录)")
        return

    try:
        from floodmind.skills.base import discover_skills
        skills = discover_skills(target)
        if not skills:
            click.echo("(无 Skill)")
            return
        for s in skills:
            click.echo(f"  {s.name} — {s.description[:60]}...")
    except Exception as e:
        click.echo(f"加载失败: {e}")


# ── config ──────────────────────────────────────────────────

@main.command("providers")
def list_providers():
    """列出所有可用的 AI Provider (provider:model)"""
    from floodmind.config.provider_registry import list_available_providers
    providers = list_available_providers()
    if not providers:
        click.echo("(无可用 Provider，请先配置 API Key)")
        return
    for p in providers:
        key_status = "✓" if p.get("api_key") else "✗"
        click.echo(f"\n  [{key_status}] {p['id']} — {p['name']}")
        click.echo(f"       base_url: {p['base_url']}")
        models = p.get("models", [])
        if models:
            click.echo(f"       models: {', '.join(models[:8])}" + ("..." if len(models) > 8 else ""))
        else:
            click.echo(f"       models: (auto-discover)")
    click.echo(f"\n  用法: --model provider/model (e.g. --model dashscope/deepseek-v4-flash)")


@main.group()
def config():
    """配置管理"""
    pass


@config.command()
def show():
    """显示当前配置"""
    from floodmind.config.settings import get_config
    import json
    cfg = get_config()
    click.echo(json.dumps(cfg, ensure_ascii=False, indent=2))


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """设置配置项 (写入 ~/.floodmind/settings.json)"""
    from floodmind.config.settings import get_config, save_config, _config_path

    cfg = get_config()

    keys = key.split(".")
    ptr = cfg
    for k in keys[:-1]:
        if k not in ptr or not isinstance(ptr[k], dict):
            ptr[k] = {}
        ptr = ptr[k]

    if value.lower() in ("true", "false"):
        value = value.lower() == "true"
    elif value.isdigit():
        value = int(value)
    elif "." in value and value.replace(".", "").isdigit():
        value = float(value)

    ptr[keys[-1]] = value
    save_config(cfg)
    config_path = _config_path()
    click.echo(f"[OK] {key} = {value} (已写入 {config_path})")


# ── 实际入口函数（被 click 命令和交互菜单共用）────────────────

def _build_cli_workspace(session_id: str):
    """CLI/native 入口：当前启动目录即 folder-first workspace。"""
    from floodmind.agent.runtime.services.workspace_service import build_folder_workspace

    return build_folder_workspace(session_id, primary_dir=Path.cwd())


def _legacy_ui_notice(entrypoint: str) -> int:
    """提示 Web/TUI 入口已进入 legacy 迁移路径。"""
    click.echo(
        f"FloodMind {entrypoint} 已进入 legacy 迁移路径：当前版本以 Python SDK 和 `floodmind run` 为核心入口。"
    )
    click.echo("请使用 `floodmind run \"任务描述\"`，或在 Python 中通过 `from floodmind import Agent, Workspace` 使用 SDK。")
    click.echo("如需临时使用旧 Web/TUI，请切换到 legacy 分支/兼容包，或直接启动旧版适配器。")
    return 0


def _run_chat_legacy(model=None, reasoning=None) -> int:
    """旧的纯文本 chat 模式（保留向后兼容）"""
    from floodmind.config.settings import settings
    from floodmind.agent.native.model_client import ModelClient
    from floodmind.memory import DualMemory
    from floodmind.agent import create_flood_agent

    if model:
        settings.model.model_name = model
    if reasoning is not None:
        settings.model.enable_reasoning = reasoning

    click.echo(f"\n  FloodMind v1.1.0  —  {settings.model.model_name}")
    click.echo("  输入 'exit' 退出, 'clear' 清空记忆, 'memory' 查看记忆\n")

    llm = ModelClient.from_settings(
        temperature=settings.model.temperature,
        max_tokens=settings.model.max_tokens,
    )
    import uuid
    sid = f"cli-chat-{uuid.uuid4().hex[:8]}"
    memory = DualMemory(
        session_id=sid,
        context_window=settings.model.context_window,
        llm=llm,
    )
    workspace = _build_cli_workspace(sid)
    agent = create_flood_agent(llm_service=llm, memory=memory, session_id=sid, workspace=workspace)

    while True:
        try:
            user_input = click.prompt("\n用户", prompt_suffix=": ", default="", show_default=False)
        except (KeyboardInterrupt, EOFError, click.Abort):
            click.echo("\n再见！")
            return 0

        if user_input.strip().lower() in ("exit", "quit", "q"):
            return 0
        if not user_input.strip():
            continue
        if user_input.strip().lower() == "clear":
            agent.clear_memory()
            click.echo("[OK] 对话历史已清空")
            continue
        if user_input.strip().lower() == "memory":
            summary = agent.get_memory_summary()
            click.echo(f"\n记忆摘要:")
            click.echo(f"  对话轮数: {summary.get('turn_count', 0)}")
            click.echo(f"  长期事实: {summary.get('long_term_count', 0)}")
            continue

        click.echo("\n助手: ", nl=False)
        try:
            for chunk in agent.stream(user_input):
                etype = chunk.get("type", "")
                if etype == "answer_delta":
                    click.echo(chunk["content"], nl=False)
                elif etype == "action_start":
                    click.echo(f"\n[调用工具: {chunk.get('tool_name', '?')}]")
                elif etype == "action_end":
                    preview = chunk.get("content", "")[:300]
                    if preview:
                        click.echo(f"[结果]: {preview}")
                elif etype == "error":
                    click.echo(f"\n[错误: {chunk.get('content', chunk)}]")
            click.echo()
        except Exception as e:
            click.echo(f"\n[错误: {e}]")


if __name__ == "__main__":
    main()
