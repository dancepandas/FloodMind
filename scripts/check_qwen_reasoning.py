import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.qwen_llm_service import QwenLLMService


def main() -> None:
    load_dotenv()

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DASHSCOPE_API_KEY")

    service = QwenLLMService(
        api_key=api_key,
        model_name=os.getenv("QWEN_MODEL", "qwen3-flash"),
        reasoning_model=os.getenv("QWEN_REASONING_MODEL", "qwen-plus"),
        temperature=0.1,
        max_tokens=512,
        enable_reasoning=True,
    )

    llm = service.get_llm()
    prompt = "请先简短思考，再回答：1+1等于几？"

    print("=== invoke ===")
    response = llm.invoke(prompt)
    print("content:", getattr(response, "content", ""))
    print("additional_kwargs keys:", sorted(list(getattr(response, "additional_kwargs", {}).keys())))
    print("reasoning_content:", getattr(response, "additional_kwargs", {}).get("reasoning_content", ""))

    print("\n=== stream ===")
    saw_reasoning = False
    for chunk in llm.stream(prompt):
        additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
        reasoning = additional_kwargs.get("reasoning_content", "")
        content = getattr(chunk, "content", "")
        if reasoning:
            saw_reasoning = True
            print("reasoning chunk:", reasoning)
        if content:
            print("content chunk:", content)

    print("\nstream reasoning seen:", saw_reasoning)


if __name__ == "__main__":
    main()
