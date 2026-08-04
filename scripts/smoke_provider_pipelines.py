"""真实 API 冒烟：deepseek / minimax / kimi 三家 pipeline 实测（key 从 docs/key.txt 读，不打印）。"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from floodmind.agent.native.model_client import ModelClient

keys = {}
for line in open(r"docs/key.txt", encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    name, _, key = re.split(r"[:：]", line, maxsplit=1)[0], None, None
    parts = re.split(r"[:：]", line, maxsplit=1)
    keys[parts[0].strip()] = parts[1].strip()

CASES = [
    ("deepseek", "https://api.deepseek.com", "deepseek-chat"),
    ("minimax", "https://api.minimaxi.com/v1", "MiniMax-M2.7"),
    ("kimi", "https://api.moonshot.cn/v1", "kimi-k2.5"),
]

PROMPT = "用一句话回答：杭州西湖的面积大约是多少？"

for provider, base_url, model in CASES:
    print("=" * 60)
    print(f"[{provider}] model={model}")
    try:
        client = ModelClient(
            api_key=keys[provider], base_url=base_url, model_name=model,
            temperature=0.3, max_tokens=512, timeout=60,
            enable_thinking=True, provider=provider,
        )
        print(f"  pipeline -> {client.pipeline.name} (conservative={client.pipeline.conservative})")

        # 1) 非流式
        msg = client.invoke(PROMPT)
        reasoning = (msg.additional_kwargs or {}).get("reasoning_content")
        usage = (msg.additional_kwargs or {}).get("usage")
        print(f"  [chat] answer: {(msg.content or '')[:80]}")
        print(f"  [chat] reasoning: {str(reasoning)[:60] if reasoning else '(none)'}")
        print(f"  [chat] usage: {usage}")

        # 2) 流式
        events = {"reasoning": 0, "token": 0, "usage": 0, "done": 0, "error": 0}
        reasoning_text, answer_text, usage_data = "", "", None
        for ev in client.stream_chat([{"role": "user", "content": PROMPT}]):
            events[ev.type] = events.get(ev.type, 0) + 1
            if ev.type == "reasoning":
                reasoning_text += ev.content
            elif ev.type == "token":
                answer_text += ev.content
            elif ev.type == "usage":
                usage_data = ev.content
            elif ev.type == "error":
                print(f"  [stream] ERROR: {ev.content[:200]}")
        print(f"  [stream] events: {events}")
        print(f"  [stream] reasoning({len(reasoning_text)} chars): {reasoning_text[:60]}")
        print(f"  [stream] answer({len(answer_text)} chars): {answer_text[:80]}")
        print(f"  [stream] usage: {usage_data}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {str(e)[:300]}")
print("=" * 60)
print("done")
