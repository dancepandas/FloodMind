#!/usr/bin/env python3
"""根据敖江案例自然语言描述解析 stationCode。"""

from __future__ import annotations

from typing import Any

DEFAULT_STATION_CODE = "33c76b8bd9384486a945c2fc7fd622eb"

STATION_LABELS = {
    "33c76b8bd9384486a945c2fc7fd622eb": "霍口水库断面预报",
    "20001": "霍口水库~山仔水库区间断面预报",
    "30001": "山仔水库~temp-1 区间断面预报",
    "40001": "temp-1~temp-2 区间断面预报",
    "GE2AG000000L": "桂湖溪流域出口断面预报",
    "GE2AF000000R": "牛溪流域出口断面预报",
}

STATION_NAME_ALIASES = {
    "33c76b8bd9384486a945c2fc7fd622eb": ["霍口水库", "霍口", "霍口断面"],
    "20001": ["霍口水库到山仔水库", "霍口到山仔", "霍口水库~山仔水库", "霍口水库-山仔水库区间"],
    "30001": ["山仔水库到temp-1", "山仔到temp-1", "山仔水库~temp-1", "山仔水库~水动力模型区间"],
    "40001": ["temp-1到temp-2", "temp-1~temp-2", "水动力模型区间"],
    "GE2AG000000L": ["桂湖溪流域出口", "桂湖", "桂湖溪流域", "桂湖出口", "桂湖溪出口"],
    "GE2AF000000R": ["牛溪流域出口", "牛溪", "牛溪流域", "牛溪出口"],
}

ALL_MAINSTREAM_AND_TRIBUTARY_CODES = [
    "33c76b8bd9384486a945c2fc7fd622eb",
    "20001",
    "30001",
    "40001",
    "GE2AG000000L",
    "GE2AF000000R",
]

DIRECT_RULES = [
    (("牛溪",), ["GE2AF000000R"], "支流出口任务，不补充上游站点"),
    (("桂湖",), ["GE2AG000000L"], "支流出口任务，不补充上游站点"),
    (("霍口水库到山仔水库", "霍口到山仔", "霍口水库~山仔水库"), ["20001"], "区间预报任务，不补充上游站点"),
    (("山仔水库到temp-1", "山仔到temp-1", "山仔水库~temp-1", "山仔水库~水动力模型区间"), ["30001"], "区间预报任务，不补充上游站点"),
    (("temp-1到temp-2", "temp-1~temp-2", "水动力模型区间"), ["40001"], "区间预报任务，不补充上游站点"),
]


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def parse_explicit_station_codes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
    else:
        parts = [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]
    return parts


def resolve_aojiang_station_codes(description: str) -> tuple[list[str], list[str]]:
    text = description.strip()
    notes: list[str] = []

    if any(keyword in text for keyword in ("最终出口", "敖江流域出口", "敖江流域未来", "流域最终出口")):
        notes.append("识别为敖江最终出口联合预报任务，已展开为主干流和支流相关 stationCode。")
        return ALL_MAINSTREAM_AND_TRIBUTARY_CODES, notes

    for aliases, station_codes, note in DIRECT_RULES:
        if _contains_any(text, aliases):
            notes.append(note)
            return station_codes, notes

    if "山仔" in text and any(keyword in text for keyword in ("入库", "入流", "来水")):
        notes.append("识别为山仔水库入库联合预报任务，已补充霍口水库及霍口~山仔区间。")
        return ["33c76b8bd9384486a945c2fc7fd622eb", "20001"], notes

    if "霍口" in text:
        notes.append("识别为霍口水库单断面预报任务。")
        return ["33c76b8bd9384486a945c2fc7fd622eb"], notes

    notes.append(f"未从敖江描述中识别出明确站点，回退默认 stationCode={DEFAULT_STATION_CODE}。")
    return [DEFAULT_STATION_CODE], notes


def describe_station_codes(station_codes: list[str]) -> str:
    return "；".join(f"{station_code}: {STATION_LABELS.get(station_code, '未命名站点')}" for station_code in station_codes)


def resolve_aojiang_station_name(name: str) -> str | None:
    text = str(name).strip()
    if not text:
        return None
    for station_code, aliases in STATION_NAME_ALIASES.items():
        if any(alias in text for alias in aliases):
            return station_code
    return None
