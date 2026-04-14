#!/usr/bin/env python3
"""根据自然语言描述和显式参数生成标准中间 Excel。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import (
    DEFAULT_STATION_CODE,
    ensure_positive_int,
    generate_time_series,
    parse_json_array,
    parse_model_types,
    write_standard_workbook,
)
from aojiang_station_resolver import describe_station_codes, parse_explicit_station_codes, resolve_aojiang_station_codes

CHINESE_NUMBER_MAP = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "二十四": 24,
}


def parse_chinese_hours(text: str) -> int | None:
    for token, value in sorted(CHINESE_NUMBER_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if token in text:
            return value
    return None


def parse_duration(text: str, keyword: str) -> int | None:
    patterns = [
        rf"{keyword}(\d+)个?小时",
        rf"{keyword}(\d+)小时",
        rf"{keyword}(\d+)h",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    if keyword in text and ("一天" in text or "1天" in text or "一整天" in text):
        return 24
    if keyword in text:
        return parse_chinese_hours(text)
    return None


def parse_uniform_rainfall(text: str, keyword: str) -> float | None:
    if keyword not in text:
        return None
    segment = text.split(keyword, 1)[1]
    for delimiter in ("，", ",", "。", ";", "；"):
        segment = segment.split(delimiter, 1)[0]
    zero_markers = ["没下雨", "无雨", "都没雨", "均为0", "为0mm", "0mm"]
    if any(marker in segment for marker in zero_markers):
        return 0.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*mm", segment)
    if match:
        return float(match.group(1))
    return None


def first_non_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def repeat_values(duration: int, explicit_values: list[float] | None, uniform_value: float | None, field_name: str) -> list[float]:
    if explicit_values is not None:
        if len(explicit_values) != duration:
            raise ValueError(f"{field_name} 长度应为 {duration}，实际为 {len(explicit_values)}")
        return [float(item) for item in explicit_values]
    if uniform_value is None:
        raise ValueError(f"无法从输入中确定 {field_name}，请显式传入对应参数")
    return [float(uniform_value)] * duration


def resolve_station_codes(case_name: str, description: str, explicit_station_codes: list[str], default_station_code: str) -> tuple[list[str], list[str]]:
    if explicit_station_codes:
        return explicit_station_codes, [f"使用显式 stationCode: {', '.join(explicit_station_codes)}"]
    if case_name.strip().lower() == "aojiang":
        return resolve_aojiang_station_codes(description)
    return [default_station_code], [f"案例 {case_name} 未配置站点解析规则，回退默认 stationCode={default_station_code}。"]


def main() -> None:
    parser = argparse.ArgumentParser(description="根据自然语言描述和显式参数生成标准中间 Excel")
    parser.add_argument("--output_file", required=True, help="输出标准中间 Excel 路径")
    parser.add_argument("--description", required=True, help="用户的自然语言描述")
    parser.add_argument("--forecast_time", required=True, help="预报起算时间，用户未明确时应传当前系统时间")
    parser.add_argument("--case_name", default="aojiang", help="案例名称，用于 metadata")
    parser.add_argument("--station_code", default=DEFAULT_STATION_CODE, help="默认 stationCode")
    parser.add_argument("--station_codes_json", help="显式 stationCode 数组 JSON，优先于自动解析")
    parser.add_argument("--model_types", default="XAJ", help="模型类型，逗号分隔")
    parser.add_argument("--time_step_hours", default="1", help="时间步长（小时），默认 1")
    parser.add_argument("--history_duration", help="历史时长，优先于文本解析")
    parser.add_argument("--future_duration", help="未来时长，优先于文本解析")
    parser.add_argument("--history_uniform_rainfall", help="历史阶段统一降雨量（mm）")
    parser.add_argument("--future_uniform_rainfall", help="未来阶段统一降雨量（mm）")
    parser.add_argument("--history_values_json", help="历史降雨数组 JSON，长度必须等于 historyDuration")
    parser.add_argument("--future_values_json", help="未来降雨数组 JSON，长度必须等于 futureDuration")
    args = parser.parse_args()

    description = args.description.strip()
    history_duration = ensure_positive_int(
        args.history_duration or parse_duration(description, "之前") or parse_duration(description, "历史"),
        "historyDuration",
    )
    future_duration = ensure_positive_int(
        args.future_duration or parse_duration(description, "未来") or parse_duration(description, "预报"),
        "futureDuration",
    )
    time_step_hours = ensure_positive_int(args.time_step_hours, "timeStepHours")

    history_values = parse_json_array(args.history_values_json, "history_values_json")
    future_values = parse_json_array(args.future_values_json, "future_values_json")
    history_uniform_rainfall = first_non_none(
        float(args.history_uniform_rainfall) if args.history_uniform_rainfall is not None else None,
        parse_uniform_rainfall(description, "之前"),
        parse_uniform_rainfall(description, "历史"),
    )
    future_uniform_rainfall = first_non_none(
        float(args.future_uniform_rainfall) if args.future_uniform_rainfall is not None else None,
        parse_uniform_rainfall(description, "未来"),
        parse_uniform_rainfall(description, "预报"),
    )

    history_series = repeat_values(history_duration, history_values, history_uniform_rainfall, "history rainfall")
    forecast_series = repeat_values(future_duration, future_values, future_uniform_rainfall, "future rainfall")
    model_types = parse_model_types(args.model_types)
    explicit_station_codes: list[str] = []
    if args.station_codes_json:
        explicit_station_codes = parse_explicit_station_codes(parse_json_array(args.station_codes_json, "station_codes_json"))
    elif args.station_code != DEFAULT_STATION_CODE:
        explicit_station_codes = parse_explicit_station_codes(args.station_code)
    station_codes, station_notes = resolve_station_codes(args.case_name, description, explicit_station_codes, args.station_code)
    history_rows = []
    forecast_rows = []
    for station_code in station_codes:
        station_history_rows, station_forecast_rows = generate_time_series(
            forecast_time=args.forecast_time,
            history_duration=history_duration,
            future_duration=future_duration,
            time_step_hours=time_step_hours,
            history_values=history_series,
            future_values=forecast_series,
            station_code=station_code,
        )
        history_rows.extend(station_history_rows)
        forecast_rows.extend(station_forecast_rows)

    metadata = {
        "caseName": args.case_name,
        "forecastTime": args.forecast_time,
        "historyDuration": history_duration,
        "futureDuration": future_duration,
        "timeStepHours": time_step_hours,
        "defaultStationCode": args.station_code,
        "stationCodes": ",".join(station_codes),
        "modelTypes": ",".join(model_types),
        "sourceType": "natural_language",
        "sourceNote": description,
    }
    notes = [
        "该文件由自然语言自动整理生成，请先检查 metadata、各 stationCode 工作表和 notes 后再转 input.json。",
        f"原始描述: {description}",
        f"stationCode 解析结果: {describe_station_codes(station_codes)}",
    ]
    notes.extend(station_notes)
    write_standard_workbook(Path(args.output_file), metadata=metadata, history_rows=history_rows, forecast_rows=forecast_rows, notes=notes)

    print(f"标准中间 Excel 已创建: {args.output_file}")
    print(f"history rows: {len(history_rows)}")
    print(f"forecast rows: {len(forecast_rows)}")
    print(f"models: {', '.join(model_types)}")


if __name__ == "__main__":
    main()
