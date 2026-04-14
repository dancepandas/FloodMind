#!/usr/bin/env python3
"""调用靖州案例水文模型接口。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.hydro_case_client import call_hydro_case_api, load_payload, save_result_if_needed

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="调用靖州案例水文模型接口")
    parser.add_argument("--input_file", help="完整请求体 JSON 文件路径")
    parser.add_argument("--payload", help="完整请求体 JSON 字符串")
    parser.add_argument("--forecast_time", help="预报时刻，格式 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--history_duration", type=int, help="历史数据时长（小时）")
    parser.add_argument("--future_duration", type=int, help="预报时长（小时）")
    parser.add_argument("--model_run_param", help="模型参数对象 JSON")
    parser.add_argument("--model_data_params", help="历史降雨数组 JSON")
    parser.add_argument(
        "--model_forecast_rainfall_params",
        help="未来降雨数组 JSON",
    )
    parser.add_argument("--base_url", default="http://192.168.30.108:3500", help="服务基础地址")
    parser.add_argument("--timeout", type=int, default=120, help="请求超时时间（秒）")
    parser.add_argument("--output_file", help="可选，结构化结果 JSON 保存路径")

    args = parser.parse_args()

    try:
        payload = load_payload(
            input_file=args.input_file,
            payload_json=args.payload,
            forecast_time=args.forecast_time,
            history_duration=args.history_duration,
            future_duration=args.future_duration,
            model_run_param=args.model_run_param,
            model_data_params=args.model_data_params,
            model_forecast_rainfall_params=args.model_forecast_rainfall_params,
        )
        result_payload = call_hydro_case_api(
            payload=payload,
            base_url=args.base_url,
            endpoint="/jz/hydro_model",
            case_name="靖州",
            timeout=args.timeout,
        )
        result = save_result_if_needed(result_payload, args.output_file)
        print(result)
    except Exception as exc:
        print(f"靖州水文模型脚本执行失败: {exc}")


if __name__ == "__main__":
    main()
