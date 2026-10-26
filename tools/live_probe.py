"""真实模型 live 探针：验证 FloodMind 完整 runtime 的真实链路。

用法（密钥只从环境变量读取，不落盘）::

    FM_BASE=... FM_KEY=... FM_MODEL=... python tools/live_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# 必须在导入 floodmind 之前隔离状态根，避免读取用户真实配置/记忆。
os.environ.setdefault("FLOODMIND_HOME", tempfile.mkdtemp(prefix="floodmind-live-home-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from floodmind.agent.api import Agent
from floodmind.agent.native.model_client import ModelClient
from floodmind.agent.runtime.contracts.workspace import Workspace

BASE = os.environ["FM_BASE"]
KEY = os.environ["FM_KEY"]
MODEL = os.environ.get("FM_MODEL", "gpt-5.6-sol")
# 自定义模型不在 settings.json catalog 中时，显式声明窗口，避免回退到默认模型窗口。
CONTEXT_WINDOW = int(os.environ["FM_CONTEXT_WINDOW"]) if os.environ.get("FM_CONTEXT_WINDOW") else None

TASKS = [
    {
        "name": "answer",
        "prompts": ["用一句话解释什么是水文模型。"],
    },
    {
        "name": "file-roundtrip",
        "prompts": ["在当前工作区创建 notes/hydrology.txt，内容写 FloodMind live test，然后读回文件确认内容。"],
    },
    {
        "name": "multi-step",
        "prompts": [
            "在当前工作区创建 data/a.txt 和 data/b.txt，各写一行数字，然后读取两个文件并把它们的数字之和写进 data/sum.txt。"
        ],
    },
    {
        "name": "subagent",
        "system_prompt": "当用户明确要求委派时，必须调用 SubAgent 工具，不要自己直接完成。",
        "prompts": [
            "必须使用 SubAgent 工具把任务委派给子代理：在当前工作区创建 delegated.txt，内容写 subagent ok。委派完成后确认文件存在。"
        ],
    },
    {
        "name": "parallel",
        "system_prompt": "当用户明确要求并行委派时，必须调用 ParallelTask 工具。",
        "prompts": [
            "必须使用 ParallelTask 并行完成两个互不依赖的任务：一个在 data/p1.txt 写入 alpha，另一个在 data/p2.txt 写入 beta。完成后确认两个文件。"
        ],
    },
    {
        "name": "error-recovery",
        "prompts": [
            "先尝试读取 data/missing.txt。如果失败，列出当前工作区下的文件，并告诉我实际有哪些文件。"
        ],
    },
    {
        "name": "permission-denied",
        "prompts": ["读取 C:/Windows/win.ini 并告诉我内容。"],
        "deny_tools": ["Bash", "Read"],
    },
    {
        "name": "memory",
        "prompts": ["记住暗号：HYDRO-4271。只回复“已记住”。", "刚才的暗号是什么？"],
    },
]


def main() -> int:
    task_filter = {name.strip() for name in os.environ.get("FM_TASKS", "").split(",") if name.strip()}
    tasks = [task for task in TASKS if not task_filter or task["name"] in task_filter]
    if not tasks:
        print(f"no task matched FM_TASKS={sorted(task_filter)}")
        return 2
    root = Path(tempfile.mkdtemp(prefix="floodmind-live-ws-"))
    workspace = Workspace.from_folder(root, session_id="live-probe").ensure()
    client = ModelClient(
        api_key=KEY,
        base_url=BASE,
        model_name=MODEL,
        temperature=0.2,
        max_tokens=2048,
        timeout=120,
        context_window=CONTEXT_WINDOW,
    )
    rows = []
    for task in tasks:
        seen = []
        approvals = []
        holder = {}
        started = time.time()

        def on_event(ev: dict) -> None:
            kind = ev.get("type")
            if kind in {
                "action_start",
                "action_end",
                "final_text",
                "error",
                "permission_ask",
                "token_usage",
                "file_generated",
            }:
                payload = {k: v for k, v in ev.items() if k != "type"}
                seen.append((kind, payload))
                print(f"  [{kind}] {json.dumps(payload, ensure_ascii=False)[:400]}")
            if kind == "permission_ask":
                # 模拟交互式宿主：收到 ASK 立即应答，验证 SDK 的 respond_permission 闭环。
                approved = holder["agent"].respond_permission(ev["ask_id"], True)
                approvals.append({"ask_id": ev["ask_id"], "approved": approved})
                print(f"  [permission_respond] approved={approved} ask_id={ev['ask_id']}")

        denied = set(task.get("deny_tools", []))
        permission_handler = (
            (lambda tool, args, _denied=denied: tool not in _denied) if denied else None
        )
        agent = Agent(
            llm=client,
            bare=False,
            workspace=workspace,
            system_prompt=task.get("system_prompt"),
            permission_handler=permission_handler,
            permission_ask_timeout=30,
            max_iterations=12,
            max_total_tokens=200_000,
            on_event=on_event,
        )
        holder["agent"] = agent
        turns = []
        for prompt in task["prompts"]:
            error = ""
            output = ""
            try:
                output = agent.run(prompt)
            except Exception as exc:  # noqa: BLE001 - 探针需要记录任何失败
                error = f"{type(exc).__name__}: {exc}"
            turns.append({"prompt": prompt, "output": output[:500], "error": error})
            if error:
                print(f"  [turn-error] {error}")
        elapsed = round(time.time() - started, 1)
        row = {
            "task": task["name"],
            "elapsed_s": elapsed,
            "usage": dict(agent.last_usage),
            "events": len(seen),
            "permission_approvals": approvals,
            "turns": turns,
            "workspace": str(root),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2))
    print("SUMMARY", json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if all(not t["error"] for r in rows for t in r["turns"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
