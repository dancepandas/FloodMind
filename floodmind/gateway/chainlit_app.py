"""FloodMind x Chainlit — 采用开源 Agent WebUI（github.com/Chainlit/chainlit）作为前端。

本模块把 FloodMind Agent 的 SDK 流式事件桥接到 Chainlit UI：
- answer_delta   → cl.Message 流式 token
- thought_delta  → cl.Step(type="thinking") 折叠思考
- action_*       → cl.Step(type="tool") 工具调用面板（输入/输出）
- permission_ask → cl.AskActionMessage 原生批准/拒绝按钮（经 AskService 应答，闭环续跑）
- file/image 产物 → cl.File / cl.Image 元素
- error          → 错误消息

运行方式：`floodmind gateway --ui chainlit`（内部执行 `chainlit run <本文件>`）。
工作区与登录遵循 gateway 默认策略：回环地址免鉴权、启动即用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import chainlit as cl

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = Path(os.environ.get("FLOODMIND_GATEWAY_WORKSPACE", Path.cwd())).resolve()

# 历史会话持久化（SQLite 数据层 + 本地无感鉴权），必须在回调注册前完成
from floodmind.gateway import chainlit_history

chainlit_history.install(_WORKSPACE_ROOT / ".floodmind" / "chainlit", _WORKSPACE_ROOT)


def _build_agent(session_id: str):
    """每个 Chainlit 会话一个完整 runtime Agent（与 gateway/agent_factory 一致）。"""
    from floodmind.agent.api import Agent
    from floodmind.agent.runtime.contracts.workspace import Workspace

    workspace = Workspace.from_folder(str(_WORKSPACE_ROOT), session_id=session_id).ensure()
    from floodmind.gateway.state import GatewayState

    return GatewayState(
        workspace_root=_WORKSPACE_ROOT, auth_token=""
    ).agent_factory(session_id)


IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Codex 桌面端风格：工具行 = 动词 + 对象 + 耗时，输出默认折叠
_TOOL_VERBS = {
    "Bash": "运行",
    "Read": "读取",
    "Write": "写入",
    "Edit": "编辑",
    "ApplyPatch": "打补丁",
    "Glob": "搜索",
    "Grep": "搜索",
    "WebFetch": "抓取",
    "WebSearch": "联网搜索",
    "GetTool": "加载工具",
    "ListTools": "列出工具",
    "CreateSkill": "创建技能",
    "SubAgent": "委派子代理",
    "ParallelTask": "并行委派",
    "Memory": "记忆",
    "Checkpoint": "存档",
    "Ask": "询问",
}
_TOOL_OBJECT_KEYS = (
    "command", "file_path", "path", "pattern", "url", "query",
    "tool_name", "skill_name", "name", "session_id", "checkpoint_id",
)
_STEP_OUTPUT_CHARS = 2000
# Codex 式降噪：内部机制类工具（渐进加载）不对用户渲染
_HIDDEN_TOOLS = {"GetTool", "ListTools"}


def _tool_verb(tool_name: str) -> str:
    return _TOOL_VERBS.get(tool_name or "", f"调用 {tool_name}" if tool_name else "调用工具")


def _tool_primary_input(tool_name: str, tool_input) -> str:
    """工具入参的主参数（Bash→命令、Read→路径…），用于展示与行标题。"""
    s = str(tool_input or "").strip()
    if not s:
        return ""
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            for k in _TOOL_OBJECT_KEYS:
                v = obj.get(k)
                if v:
                    return str(v).strip()
            return ""
    except Exception:
        pass
    return s.splitlines()[0][:120]


def _truncate_output(text: str, limit: int = _STEP_OUTPUT_CHARS) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    lines = s.count("\n") + 1
    return s[:limit] + f"\n\n… +{lines} 行（点击展开查看已截断内容，完整输出见会话产物）"


def _humanize_delegate_result(raw) -> str:
    """委派工具（SubAgent/ParallelTask）的结果 JSON → 可读文本。

    原始输出是 stage/status/user_goal/task/summary 等字段的 JSON 转储，对用户
    没有阅读价值；提取状态、任务与 summary 摘要。解析失败时原样返回。
    """
    s = str(raw or "").strip()
    if not s:
        return s
    try:
        obj = json.loads(s)
    except Exception:
        return s
    if not isinstance(obj, dict) or "summary" not in obj:
        return s
    parts = []
    status = obj.get("status") or ""
    if status:
        parts.append(f"[{status}]")
    task = str(obj.get("task") or "").strip()
    if task:
        parts.append(f"任务：{task.splitlines()[0][:160]}")
    summary = str(obj.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    artifacts = obj.get("artifacts") or []
    if artifacts:
        names = ", ".join(
            a.get("name") or a.get("path") or str(a) if isinstance(a, dict) else str(a)
            for a in artifacts[:8]
        )
        parts.append(f"产物：{names}")
    return "\n\n".join(parts)


def _is_event(event: dict, *types: str) -> bool:
    return event.get("type") in types


@cl.on_chat_start
async def on_chat_start() -> None:
    # thread_id 由 Chainlit 持久化且跨重启稳定，确定性映射到 FloodMind session id，
    # 这样点击历史会话恢复（on_chat_resume）后继续写入同一份 Journal。
    thread_id = cl.context.session.thread_id
    session_id = chainlit_history.session_id_for_thread(thread_id)
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("agent", None)
    cl.user_session.set("thread_name", None)
    # 不发 greeting 消息：新会话保持 Chainlit 原生 welcome 空页（品牌 logo 居中），
    # 模型/会话信息按需向 agent 询问即可。


@cl.on_stop
async def on_stop() -> None:
    agent = cl.user_session.get("agent")
    abort = cl.user_session.get("abort_event")
    if abort is not None:
        abort.set()


@cl.on_chat_resume
async def on_chat_resume(thread) -> None:
    """点击历史会话：恢复对应的 FloodMind session（Journal 连续）。"""
    metadata = chainlit_history.thread_metadata(thread.get("metadata"))
    session_id = metadata.get("floodmind_session_id") or chainlit_history.session_id_for_thread(
        thread.get("id", "")
    )
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("agent", None)
    cl.user_session.set("abort_event", None)
    cl.user_session.set("thread_name", thread.get("name"))
    logger.info("Chainlit 恢复历史会话 thread=%s -> session=%s", thread.get("id"), session_id)


async def _sync_thread_meta() -> None:
    """把 thread 与 FloodMind session 的绑定写进数据层（含自动命名）。"""
    try:
        from chainlit.data import get_data_layer

        layer = get_data_layer()
        if layer is None:
            return
        existing_name = cl.user_session.get("thread_name")
        name = existing_name
        if not name:
            text = str(cl.user_session.get("first_user_text") or "")
            name = text.strip().replace("\n", " ")[:40] or None
        await layer.update_thread(
            cl.context.session.thread_id,
            name=name,
            metadata={"floodmind_session_id": cl.user_session.get("session_id")},
        )
    except Exception as exc:
        logger.debug("Chainlit thread 元数据同步失败（忽略）: %s", exc)


async def _drain_events(
    agent,
    user_text: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    abort_event: threading.Event,
) -> None:
    """在 worker 线程中驱动同步生成器，把事件推给 asyncio 队列。

    注意：cl.user_session 是 contextvar 绑定，worker 线程里不可见——
    所有必需的入参（消息文本）必须显式传入。
    """

    def _run() -> None:
        try:
            for event in agent.stream(user_text, abort_check=abort_event.is_set):
                asyncio.run_coroutine_threadsafe(queue.put(dict(event)), loop)
        except Exception as exc:  # 把异常转为 error 事件，UI 可见
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "content": str(exc)}), loop
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    threading.Thread(target=_run, daemon=True, name="floodmind-chainlit-run").start()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    agent = cl.user_session.get("agent")
    if agent is None:
        session_id = cl.user_session.get("session_id")
        if not session_id:
            session_id = chainlit_history.session_id_for_thread(
                cl.context.session.thread_id
            )
            cl.user_session.set("session_id", session_id)
        agent = _build_agent(session_id)
        cl.user_session.set("agent", agent)
        cl.user_session.set("first_user_text", message.content)
        await _sync_thread_meta()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    abort_event = threading.Event()
    cl.user_session.set("abort_event", abort_event)

    # Codex 桌面端式回合折叠：折叠头自带耗时/工具次数/token 摘要，运行中 shimmer，
    # 结束自动收起。状态信息全在折叠头内显示，无需右侧 Tasks 侧栏重复呈现。
    run_failed = False
    run_completed = False  # llm_step_end reason=stop 后置 true，前端用此隐藏活动摘要

    await _drain_events(agent, message.content, queue, loop, abort_event)

    # ── dsh-turn-fold 风格回合折叠（设计 v2）──
    # 折叠栏 = 容器（cl.Step type=run），运行中默认折叠，结束自动收起。
    # 折叠栏下方实时显示最近 2 条过程活动摘要（前 15 字 + "..."），让用户不展开
    # 也能看到进度尾巴；点击展开折叠栏时这些摘要自动隐藏（已能看到完整内容）。
    # 折叠栏内部仍装所有思考/工具/叙述（点击展开可见）。
    # 最终答复独立为顶层 message。
    # 无工具/思考的纯问答不创建折叠栏，直接顶层流式（行为不变）。
    answer = cl.Message(content="", author="FloodMind")
    think_step: Optional[cl.Step] = None
    think_started = 0.0
    tool_steps: dict = {}
    turn: Optional[cl.Step] = None
    turn_started = time.time()
    turn_stats = {"tools": 0, "tokens": 0}
    final_sent = False
    # 容器内的当前叙述段
    in_turn_narr: Optional[cl.Step] = None
    # 折叠栏下方的最近 2 条过程活动摘要（cl.Message，id 留待 JS 切换显隐）
    activity_summaries: list = []  # 元素 = cl.Message（已 send 的）

    async def _ensure_turn() -> None:
        nonlocal turn, turn_started
        if turn is None:
            turn_started = time.time()
            # 容器：type=run 让前端递归渲染子 step。
            # default_open=False：默认折叠，运行中/结束后都收起——用户主动点击
            # 展开才看完整过程活动。折叠栏下方实时显示最近 2 条 15 字摘要作为
            # 进度尾巴。
            turn = cl.Step(
                name="正在工作", type="run", default_open=False,
            )
            await turn.__aenter__()

    async def _close_think() -> None:
        nonlocal think_step
        if think_step is None:
            return
        think_step.name = f"思考了 {time.time() - think_started:.1f} 秒"
        try:
            await think_step.update()
        except Exception:
            pass
        await think_step.__aexit__(None, None, None)
        think_step = None

    async def _close_in_turn_narr() -> str:
        """关闭容器内当前叙述段，返回完整文本。Step 走 __aexit__ 路径，关闭前
        用叙述首行更新 name（让折叠头在 finished 状态下显示本段首句）。"""
        nonlocal in_turn_narr
        if in_turn_narr is None:
            return ""
        text = in_turn_narr.output or ""
        first = text.strip().splitlines()[0][:60] if text.strip() else ""
        if first:
            in_turn_narr.name = first
            try:
                await in_turn_narr.update()
            except Exception:
                pass
        try:
            await in_turn_narr.__aexit__(None, None, None)
        except Exception:
            pass
        in_turn_narr = None
        return text

    async def _emit_activity(label: str, content: str = "") -> None:
        """在折叠栏下方追加一条过程活动摘要（前 15 字 + "..."）。
        维护最近 2 条；超出 2 条的最旧那条标记为过期让 JS 隐藏。
        content 用 markdown code 反引号包 sentinel——反引号内是字面文本
        不会被 markdown 解析。前端 textContent 包含 'data-fm-act="' 识别。
        视觉上是普通正文（反引号部分会作为 <code> 元素渲染，可接受）。
        """
        text = (content or "").strip().replace("\n", " ")
        summary = text[:15] + ("…" if len(text) > 15 else "")
        idx = len(activity_summaries)
        # 反引号包 sentinel——markdown 把 ``...`` 渲染为 <code>，原样保留内容
        line = f'`<span data-fm-act="{idx}"></span>` **{label}** {summary}'
        msg = cl.Message(content=line, author="FloodMind")
        await msg.send()
        activity_summaries.append(msg)

    while True:
        ev = await queue.get()
        if ev is None:
            break
        etype = ev.get("type", "")

        # 子代理事件分流：StepEventBus 给所有事件注入 step_key（子代理事件）——
        # 子代理的叙述/思考/工具都不在主代理 UI 单独展示，其结果统一由主代理
        # ParallelTask 工具步骤的 action_end 一次性呈现（已经过 _humanize_delegate_result
        # 转可读文本）。
        if ev.get("step_key"):
            if etype == "llm_step_end":
                # 仅累加 token 用量到主代理统计（turn 摘要的 token 数）
                toks = ev.get("tokens") or {}
                turn_stats["tokens"] += int(toks.get("total_tokens") or 0)
            # 其他子代理事件一律不进主代理 UI 容器
            continue

        if etype == "answer_delta":
            # 叙述策略：
            # - 容器未创建：立即创建 turn 容器，叙述作为 in_turn_narr Message 流入
            #   屏幕（避免"容器创建前的叙述定格在顶层"导致双重回答）
            # - 容器已存在：直接流入当前 in_turn_narr
            # 最终答复由 llm_step_end(reason=stop) 单独发送为顶层消息。
            # 注意：in_turn_narr 用 cl.Message(parent_id=turn.id) 而非 cl.Step
            # ——Step type=run 在 Radix Accordion 嵌套场景下 default_open 不可靠
            # （子容器 header 仍显示折叠态，主体藏起来）。JS 兜底：DOM 注入后
            # 主动 click 折叠头切到 open 状态再拦截切换。
            # Message 方案被弃：cl.Message(parent_id=turn.id) 不被前端作为 turn
            # 的子级渲染，会冒到顶层 message 列表（实测出现两个顶层 message）。
            if not final_sent:
                if turn is None:
                    await _ensure_turn()
                if in_turn_narr is None:
                    in_turn_narr = cl.Step(
                        name="思考与说明", type="run",
                        default_open=True, parent_id=turn.id,
                    )
                    await in_turn_narr.__aenter__()
                await in_turn_narr.stream_token(ev.get("content") or "")
        elif etype == "final_text":
            # 兜底：异常终止时补发完整答案，避免最终答复丢失
            if not final_sent and ev.get("content"):
                await _close_think()
                await _close_in_turn_narr()
                await cl.Message(content=ev["content"], author="FloodMind").send()
                final_sent = True
        elif etype == "thought_delta":
            await _ensure_turn()
            # 思考与叙述互互，不能同时存在：关闭当前容器内叙述
            await _close_in_turn_narr()
            if think_step is None:
                think_started = time.time()
                think_step = cl.Step(
                    name="思考中", type="thinking",
                    default_open=False, parent_id=turn.id,
                )
                await think_step.__aenter__()
            await think_step.stream_token(ev.get("content") or "")
        elif etype == "llm_step_end":
            toks = ev.get("tokens") or {}
            turn_stats["tokens"] += int(toks.get("total_tokens") or 0)
            reason = str(ev.get("finish_reason") or "")
            if reason == "aborted":
                run_failed = True
            await _close_think()
            last_narr = await _close_in_turn_narr()
            # 折叠栏下方追加叙述摘要（前 15 字）—— 仅在容器内累积的叙述非空时
            if last_narr and reason != "stop":
                await _emit_activity("思考", last_narr)
            # reason=stop：本轮叙述即最终答复——独立顶层消息
            # 不传 author：用户偏好正式回答前面不要 avatar（品牌只在折叠栏呈现）。
            if reason == "stop" and not final_sent:
                # 优先用容器内累积的叙述作为最终答复（完整、连续）
                src = last_narr or answer.content
                if src:
                    await cl.Message(content=src).send()
                    final_sent = True
                # 任务完成标记——前端通过这个判断是否隐藏活动摘要
                # 不用发 sentinel message 避免漏出
                run_completed = True
                # 重置缓冲避免下一轮重复
                answer = cl.Message(content="", author="FloodMind")
        elif etype == "action_start":
            if (ev.get("tool_name") or "") in _HIDDEN_TOOLS:
                continue
            await _ensure_turn()
            await _close_think()
            await _close_in_turn_narr()
            call_id = ev.get("call_id") or f"{ev.get('tool_name')}@{id(ev)}"
            verb = _tool_verb(ev.get("tool_name") or "")
            obj = _tool_primary_input(ev.get("tool_name") or "", ev.get("tool_input"))
            label = f"{verb} `{obj}`" if obj else verb
            # 折叠栏下方摘要（前 15 字）
            await _emit_activity(verb, obj or ev.get("tool_name", ""))
            step = cl.Step(name=label, type="tool", default_open=False, parent_id=turn.id)
            await step.__aenter__()
            if obj:
                await step.stream_token(f"```\n{obj[:2000]}\n```\n")
            tool_steps[call_id] = (step, verb, obj, time.time())
        elif etype == "action_end":
            if (ev.get("tool_name") or "") in _HIDDEN_TOOLS:
                continue
            call_id = ev.get("call_id") or f"{ev.get('tool_name')}@"
            entry = tool_steps.pop(call_id, None)
            if entry is not None:
                step, verb, obj, started = entry
                status = ev.get("status")
                output = ev.get("content")
                if (ev.get("tool_name") or "") in ("SubAgent", "ParallelTask"):
                    output = _humanize_delegate_result(output)
                output = _truncate_output(output)
                if output:
                    await step.stream_token(f"```\n{output}\n```")
                mark = "" if status == "completed" else "✗ "
                obj_part = f" `{obj}`" if obj else ""
                step.name = f"{mark}{verb}{obj_part} · {time.time() - started:.1f}s"
                await step.update()
                await step.__aexit__(None, None, None)
                if status == "completed":
                    turn_stats["tools"] += 1
        elif etype == "workflow_plan":
            pass
        elif etype == "permission_ask":
            # 收尾未完成的叙述流（顶层的或容器内的）
            if answer.content and not final_sent:
                await cl.Message(content=answer.content, author="FloodMind").send()
                answer = cl.Message(content="", author="FloodMind")
            await _close_in_turn_narr()
            await _close_think()
            # 审批卡必须挂到根（开 Step 会通过 stream_start 留下 stub 消息占 local_steps）
            from chainlit.context import local_steps

            local_steps.set([])
            ask_id = ev.get("ask_id", "")
            tool_name = ev.get("tool_name", "")
            tool_input = str(ev.get("tool_input", ""))
            await cl.Message(
                content=(
                    f"**需要你的批准：{tool_name}**\n\n"
                    f"{ev.get('reason', '')}\n\n"
                    f"```\n{tool_input[:600]}\n```"
                ),
                actions=[
                    cl.Action(name="fm_approve", payload={"ask_id": ask_id, "approved": True}, label="允许"),
                    cl.Action(name="fm_deny", payload={"ask_id": ask_id, "approved": False}, label="拒绝"),
                ],
                author="FloodMind",
            ).send()
            try:
                from chainlit.context import context as _cl_context

                await _cl_context.emitter.task_end()
            except Exception:
                pass
        elif etype in ("file_generated", "image_generated"):
            path = ev.get("download_url") or ev.get("filepath") or ""
            name = ev.get("filename") or ev.get("file_name") or "artifact"
            p = Path(path)
            if p.is_file():
                if p.suffix.lower() in IMG_EXTS:
                    element = cl.Image(name=name, path=str(p), display="inline")
                else:
                    element = cl.File(name=name, path=str(p))
                answer.elements = list(answer.elements or []) + [element]
        elif etype == "llm_token_error":
            run_failed = True
            await cl.Message(content="**LLM Token 余额不足**，请充值后后重试。", author="FloodMind").send()
        elif etype == "error":
            run_failed = True
            content = ev.get("content") or "未知错误"
            # LLM 提供商被风控/限流时把 HTML 错误页（<!DOCTYPE ... <title>Error - Request Blocked</title>）
            # 当成普通 content 抛上来——直接把 HTML 糊在 chat 里体验极差，识别后换友好提示。
            if isinstance(content, str) and ("<!DOCTYPE" in content or "<html" in content.lower()):
                content = (
                    "LLM 端点返回了非 JSON 错误（疑似触发平台风控/限流），"
                    "请稍后重试或切换 provider。"
                )
            await cl.Message(content=f"**出错了：** {content}", author="FloodMind").send()

    # 收尾：思考/工具步骤关闭，回合容器回填摘要行
    await _close_think()
    await _close_in_turn_narr()
    for entry in list(tool_steps.values()):
        step = entry[0] if isinstance(entry, tuple) else entry
        try:
            await step.__aexit__(None, None, None)
        except Exception:
            pass
    if turn is not None:
        dur = time.time() - turn_started
        if run_failed:
            turn.name = f"失败 · {dur:.1f}s"
        else:
            tok = ""
            if turn_stats["tokens"] >= 1000:
                tok = f" · {turn_stats['tokens'] / 1000:.1f}k tokens"
            elif turn_stats["tokens"]:
                tok = f" · {turn_stats['tokens']} tokens"
            tool_part = f" · {turn_stats['tools']} 次工具调用" if turn_stats["tools"] else ""
            turn.name = f"已完成 · {dur:.1f}s{tool_part}{tok}"
        try:
            await turn.update()
        except Exception:
            pass
        await turn.__aexit__(None, None, None)
    if answer.content or answer.elements:
        await answer.send()
    cl.user_session.set("abort_event", None)


async def _respond_permission(action: cl.Action) -> None:
    """审批按钮回调：把批准/拒绝送回 AskService，executor 在等待点续跑。"""
    from floodmind.agent.runtime.services.ask_service import get_ask_service
    from floodmind.agent.runtime.contracts.permissions import PermissionAskResponse

    payload = action.payload or {}
    ask_id = str(payload.get("ask_id", ""))
    approved = bool(payload.get("approved"))
    try:
        get_ask_service().respond(
            PermissionAskResponse(
                session_id=cl.user_session.get("session_id"),
                ask_id=ask_id,
                approved=approved,
            )
        )
        await cl.Message(
            content="已允许，继续执行。" if approved else "已拒绝该操作。",
            author="FloodMind",
        ).send()
        # 恢复运行中状态（停止按钮/输入区 loading），executor 已在续跑
        try:
            from chainlit.context import context as _cl_context

            await _cl_context.emitter.task_start()
        except Exception:
            pass
    except Exception as exc:
        await cl.Message(
            content=f"审批响应失败（可能已超时）：{exc}", author="FloodMind"
        ).send()


@cl.action_callback("fm_approve")
async def _on_approve(action: cl.Action) -> None:
    await _respond_permission(action)


@cl.action_callback("fm_deny")
async def _on_deny(action: cl.Action) -> None:
    await _respond_permission(action)
