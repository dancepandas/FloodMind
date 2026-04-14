#!/usr/bin/env python3
"""把标准中间 Excel 转成水文接口 input.json。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import (
    align_rows_to_grid,
    DEFAULT_STATION_CODE,
    build_model_run_param,
    ensure_positive_int,
    infer_durations,
    normalize_rainfall_rows,
    parse_model_types,
    read_standard_workbook,
    validate_station_counts,
)


def build_payload(input_path: Path) -> dict:
    workbook_data = read_standard_workbook(input_path)
    metadata = workbook_data["metadata"]

    forecast_time = metadata.get("forecastTime", "").strip()
    if not forecast_time:
        raise ValueError("metadata.forecastTime 不能为空")
    time_step_hours = ensure_positive_int(metadata.get("timeStepHours") or 1, "metadata.timeStepHours")
    default_station_code = metadata.get("defaultStationCode", "").strip() or DEFAULT_STATION_CODE
    model_types = parse_model_types(metadata.get("modelTypes"))

    history_rows = normalize_rainfall_rows(workbook_data["history_rows"], default_station_code)
    forecast_rows = normalize_rainfall_rows(workbook_data["forecast_rows"], default_station_code)
    history_rows = align_rows_to_grid(history_rows, forecast_time=forecast_time, time_step_hours=time_step_hours)
    forecast_rows = align_rows_to_grid(forecast_rows, forecast_time=forecast_time, time_step_hours=time_step_hours)
    raw_history_duration = metadata.get("historyDuration")
    raw_future_duration = metadata.get("futureDuration")
    if raw_history_duration in (None, "") or raw_future_duration in (None, ""):
        history_duration, future_duration = infer_durations(history_rows, forecast_rows, forecast_time, time_step_hours)
    else:
        history_duration = ensure_positive_int(raw_history_duration, "metadata.historyDuration")
        future_duration = ensure_positive_int(raw_future_duration, "metadata.futureDuration")
    errors = validate_station_counts(
        history_rows=history_rows,
        forecast_rows=forecast_rows,
        history_duration=history_duration,
        future_duration=future_duration,
        default_station_code=default_station_code,
    )
    if errors:
        raise ValueError("; ".join(errors))

    model_data_params = [
        {
            "time": row["time"],
            "rainfallValue": row["rainfallValue"],
            "stationCode": row["stationCode"],
        }
        for row in history_rows
    ]
    model_forecast_rainfall_params = [
        {
            "time": row["time"],
            "rainfallValue": row["rainfallValue"],
            "stationCode": row["stationCode"],
        }
        for row in forecast_rows
    ]

    return {
        "forecastTime": forecast_time,
        "historyDuration": history_duration,
        "futureDuration": future_duration,
        "modelDataParams": model_data_params,
        "modelForecastRainfallParams": model_forecast_rainfall_params,
        "modelRunParam": build_model_run_param(model_types),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="把标准中间 Excel 转成水文接口 input.json")
    parser.add_argument("--input_file", required=True, help="标准中间 Excel 文件路径")
    parser.add_argument("--output_file", required=True, help="输出 input.json 文件路径")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    output_path = Path(args.output_file)

    payload = build_payload(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"input.json 已创建: {output_path}")
    print(f"history rows: {len(payload['modelDataParams'])}")
    print(f"forecast rows: {len(payload['modelForecastRainfallParams'])}")
    print(f"models: {', '.join(payload['modelRunParam'].keys())}")


if __name__ == "__main__":
    main()
