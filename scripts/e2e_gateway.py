"""Gateway 端到端验证：SSE 对话 + 权限 ASK 批准闭环（开发用脚本）。

用法：先 `floodmind gateway --port 8399 --workspace <scratch>` 启动网关，
再 `FLOODMIND_TEST_TOKEN=<token> python scripts/e2e_gateway.py`。
"""
import json
import os
import sys
from pathlib import Path

import requests

BASE = os.getenv("FLOODMIND_GATEWAY_URL", "http://127.0.0.1:8399")


def _load_token() -> str:
    token = os.getenv("FLOODMIND_TEST_TOKEN", "").strip()
    if token:
        return token
    settings = Path.home() / ".floodmind" / "settings.json"
    if settings.exists():
        return str(json.loads(settings.read_text(encoding="utf-8")).get("gateway", {}).get("auth_token", ""))
    return ""


TOKEN = _load_token()
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json; charset=utf-8"}

session_id = "e2e-perm-1"
body = {
    "session_id": session_id,
    "message": "请用 Bash 工具执行命令：echo e2e-ok > e2e_note.txt （在工作区根目录创建文件）。完成后告诉我文件内容。",
}
final_text = ""
ask_handled = None
tool_ends = []

with requests.post(f"{BASE}/api/chat", headers=H, json=body, stream=True, timeout=300) as resp:
    print("HTTP", resp.status_code)
    buf = ""
    for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
        buf += chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            line = next((l for l in raw.split("\n") if l.startswith("data: ")), None)
            if not line:
                continue
            ev = json.loads(line[6:])
            t = ev.get("type")
            if t == "permission_ask":
                ask_id = ev["ask_id"]
                print("[ASK]", ev.get("tool_name"), ev.get("reason", "")[:60])
                r = requests.post(
                    f"{BASE}/api/permission/respond", headers=H,
                    json={"session_id": session_id, "ask_id": ask_id, "approved": True},
                )
                print("[RESPOND]", r.status_code, r.json())
                ask_handled = ask_id
            elif t == "action_end":
                tool_ends.append((ev.get("tool_name"), ev.get("status")))
            elif t == "final_text":
                final_text = ev.get("content", "")
            elif t == "__done__":
                print("[DONE]")
                break
            elif t == "error":
                print("[ERROR]", ev.get("content", "")[:200])

print("tool_ends:", tool_ends)
print("ask_handled:", bool(ask_handled))
print("final:", final_text[:120])
ok = bool(ask_handled) and ("e2e-ok" in final_text or any(s == "completed" for _, s in tool_ends))
print("E2E PERMISSION:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
