#!/usr/bin/env python3
"""
案例水文模型接口调用辅助模块。

为敖江、靖州等案例 skill 提供统一的参数解析、请求校验、HTTP 调用和结果格式化能力。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

REQUIRED_TOP_LEVEL_FIELDS = [
    "forecastTime",
    "historyDuration",
    "futureDuration",
    "modelDataParams",
    "modelForecastRainfallParams",
    "modelRunParam",
]


def save_result_if_needed(result_payload: dict[str, Any], output_file: str | None) -> str:
    """按需将结构化结果保存为 JSON 文件，并返回补充后的输出文本。"""
    if not output_file:
        return result_payload.get("result_text", "")

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"{result_payload.get('result_text', '')}\n\n结果已保存到: {output_path}"


def build_result_payload(
    *,
    case_name: str,
    success: bool,
    payload: dict[str, Any],
    base_url: str,
    endpoint: str,
    result_text: str,
    response_json: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """构造统一的 JSON 落盘结果。"""
    return {
        "case_name": case_name,
        "success": success,
        "request": {
            "base_url": base_url,
            "endpoint": endpoint,
            "payload": payload,
        },
        "response": response_json,
        "error": error_message,
        "result_text": result_text,
    }


def parse_json_arg(raw: str | None, arg_name: str) -> Any:
    """解析 JSON 字符串参数。"""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"参数 {arg_name} 不是合法 JSON: {exc}") from exc


def load_payload(
    *,
    input_file: str | None,
    payload_json: str | None,
    forecast_time: str | None,
    history_duration: int | None,
    future_duration: int | None,
    model_run_param: str | None,
    model_data_params: str | None,
    model_forecast_rainfall_params: str | None,
) -> dict[str, Any]:
    """从文件、整段 JSON 或分字段参数构造请求体。"""
    if input_file:
        payload_path = Path(input_file)
        if not payload_path.exists():
            raise ValueError(f"输入文件不存在: {input_file}")
        try:
            return json.loads(payload_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"输入文件不是合法 JSON: {exc}") from exc

    if payload_json:
        payload = parse_json_arg(payload_json, "--payload")
        if not isinstance(payload, dict):
            raise ValueError("参数 --payload 必须是 JSON 对象")
        return payload

    inline_payload = {
        "forecastTime": forecast_time,
        "historyDuration": history_duration,
        "futureDuration": future_duration,
        "modelRunParam": parse_json_arg(model_run_param, "--model_run_param"),
        "modelDataParams": parse_json_arg(model_data_params, "--model_data_params"),
        "modelForecastRainfallParams": parse_json_arg(
            model_forecast_rainfall_params,
            "--model_forecast_rainfall_params",
        ),
    }
    if any(value is not None for value in inline_payload.values()):
        return {key: value for key, value in inline_payload.items() if value is not None}

    raise ValueError(
        "缺少输入。请提供 --input_file、--payload，或完整的分字段参数。"
    )


def validate_payload(payload: dict[str, Any]) -> list[str]:
    """校验请求体必要字段。"""
    errors: list[str] = []

    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in payload or payload[field] in (None, ""):
            errors.append(f"缺少必填字段: {field}")

    for list_field in ["modelDataParams", "modelForecastRainfallParams"]:
        value = payload.get(list_field)
        if value is None:
            continue
        if not isinstance(value, list) or not value:
            errors.append(f"字段 {list_field} 必须是非空数组")
            continue

        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                errors.append(f"字段 {list_field} 第 {index} 项必须是对象")
                continue
            if not item.get("time"):
                errors.append(f"字段 {list_field} 第 {index} 项缺少 time")
            if "rainfallValue" not in item:
                errors.append(f"字段 {list_field} 第 {index} 项缺少 rainfallValue")
            if not item.get("stationCode") and not item.get("sectionCode"):
                errors.append(
                    f"字段 {list_field} 第 {index} 项缺少 stationCode 或 sectionCode"
                )

    model_run_param = payload.get("modelRunParam")
    if model_run_param is not None:
        if not isinstance(model_run_param, dict) or not model_run_param:
            errors.append("字段 modelRunParam 必须是非空对象")
        else:
            supported_models = {"XAJ", "GR4J", "HYMOD", "MASKGEN"}
            actual_models = set(model_run_param.keys())
            if not actual_models.intersection(supported_models):
                errors.append(
                    "字段 modelRunParam 至少需要包含一个模型键: XAJ / GR4J / HYMOD / MASKGEN"
                )

    return errors


def build_url(base_url: str, endpoint: str) -> str:
    """拼接完整接口地址。"""
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def call_hydro_case_api(
    *,
    payload: dict[str, Any],
    base_url: str,
    endpoint: str,
    case_name: str,
    timeout: int,
) -> dict[str, Any]:
    """调用案例水文接口并返回文本与结构化结果。"""
    errors = validate_payload(payload)
    if errors:
        result_text = "请求体校验失败:\n- " + "\n- ".join(errors)
        return build_result_payload(
            case_name=case_name,
            success=False,
            payload=payload,
            base_url=base_url,
            endpoint=endpoint,
            result_text=result_text,
            error_message=result_text,
        )

    url = build_url(base_url, endpoint)
    logger.info("开始调用 %s 水文接口: %s", case_name, url)

    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        result_text = f"{case_name} 水文模型调用失败：请求超时（>{timeout} 秒）"
        return build_result_payload(
            case_name=case_name,
            success=False,
            payload=payload,
            base_url=base_url,
            endpoint=endpoint,
            result_text=result_text,
            error_message=result_text,
        )
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        result_text = f"{case_name} 水文模型调用失败：HTTP {response.status_code}\n{body}"
        return build_result_payload(
            case_name=case_name,
            success=False,
            payload=payload,
            base_url=base_url,
            endpoint=endpoint,
            result_text=result_text,
            error_message=result_text,
        )
    except requests.exceptions.RequestException as exc:
        result_text = f"{case_name} 水文模型调用失败：网络错误 - {exc}"
        return build_result_payload(
            case_name=case_name,
            success=False,
            payload=payload,
            base_url=base_url,
            endpoint=endpoint,
            result_text=result_text,
            error_message=result_text,
        )

    try:
        result = response.json()
    except ValueError:
        result_text = f"{case_name} 水文模型调用失败：响应不是合法 JSON\n{response.text}"
        return build_result_payload(
            case_name=case_name,
            success=False,
            payload=payload,
            base_url=base_url,
            endpoint=endpoint,
            result_text=result_text,
            error_message=result_text,
        )

    result_text = format_result(
        case_name=case_name,
        base_url=base_url,
        endpoint=endpoint,
        payload=payload,
        result=result,
    )
    return build_result_payload(
        case_name=case_name,
        success=True,
        payload=payload,
        base_url=base_url,
        endpoint=endpoint,
        result_text=result_text,
        response_json=result,
    )


def format_result(
    *,
    case_name: str,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> str:
    """把接口响应整理成 agent 更容易消费的 Markdown。"""
    data = result.get("data") or []
    failed_models = result.get("failed_models") or []
    message = result.get("message", "")
    code = result.get("code", "")

    lines = [
        f"## {case_name} 水文模型结果",
        "",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| 接口地址 | {build_url(base_url, endpoint)} |",
        f"| forecastTime | {payload.get('forecastTime', '')} |",
        f"| historyDuration | {payload.get('historyDuration', '')} |",
        f"| futureDuration | {payload.get('futureDuration', '')} |",
        f"| 返回状态码 | {code} |",
        f"| 返回条数 | {len(data) if isinstance(data, list) else 0} |",
        f"| failed_models | {json.dumps(failed_models, ensure_ascii=False)} |",
        f"| message | {message} |",
        "",
    ]

    if isinstance(data, list) and data:
        lines.extend([
            "## 结果明细",
            "",
            "| 时间 | 流量 | 模型 | 断面 | 降雨量 |",
            "|------|------|------|------|--------|",
        ])
        for item in data:
            if not isinstance(item, dict):
                continue
            lines.append(
                "| {time} | {flow} | {model} | {section} | {rainfall} |".format(
                    time=item.get("time", ""),
                    flow=item.get("flow", ""),
                    model=item.get("modelType", ""),
                    section=item.get("sectionCode", item.get("stationCode", "")),
                    rainfall=item.get("rainfallValue", ""),
                )
            )
    else:
        lines.extend([
            "## 原始响应",
            "",
            "```json",
            json.dumps(result, ensure_ascii=False, indent=2),
            "```",
        ])

    return "\n".join(lines)
