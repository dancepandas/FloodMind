import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from floodmind.agent.native.model_client import ModelClient


def main() -> None:
    load_dotenv()

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DASHSCOPE_API_KEY")

    reasoning_prompt = "请先简短思考，再回答：1+1 等于几？"

    # === invoke 非流式调用，带推理 ===
    print("=== invoke (enable_thinking=True) ===")
    service = ModelClient.from_settings(
        model_name=os.getenv("QWEN_REASONING_MODEL", "qwen-plus"),
        temperature=0.1,
        max_tokens=512,
        enable_thinking=True,
    )
    response = service.invoke(reasoning_prompt)
    print("content:", getattr(response, "content", ""))
    print("additional_kwargs keys:", sorted(list(getattr(response, "additional_kwargs", {}).keys())))
    print("reasoning_content:", getattr(response, "additional_kwargs", {}).get("reasoning_content", ""))

    # === invoke 普通调用，无推理 ===
    print("\n=== invoke (enable_thinking=False) ===")
    service2 = ModelClient.from_settings(
        model_name=os.getenv("QWEN_MODEL", "qwen3-flash"),
        temperature=0.1,
        max_tokens=512,
        enable_thinking=False,
    )
    response2 = service2.invoke("你好，1+1=?")
    print("content:", getattr(response2, "content", ""))

    # === stream_chat 流式调用，观察 reasoning / token / done 事件 ===
    print("\n=== stream_chat ===")
    saw_reasoning = False
    messages = [{"role": "user", "content": reasoning_prompt}]
    service3 = ModelClient.from_settings(
        model_name=os.getenv("QWEN_REASONING_MODEL", "qwen-plus"),
        temperature=0.1,
        max_tokens=512,
        enable_thinking=True,
    )
    for event in service3.stream_chat(messages):
        if event.type == "reasoning":
            saw_reasoning = True
            print("reasoning chunk:", event.content)
        elif event.type == "token":
            print("content chunk:", event.content)
        elif event.type in ("done", "error", "timeout", "usage"):
            print(f"[{event.type}] {event.content[:80] if event.content else ''}")

    print("\nstream reasoning seen:", saw_reasoning)


if __name__ == "__main__":
    main()
