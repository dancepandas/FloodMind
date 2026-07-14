# input.json 构建详细示例

以下示例覆盖四种任务类型的完整 `input.json` 构建过程。

## 示例 1：联合预报 — 山仔水库未来 3 小时入库流量

1. 目标站点：`山仔水库`
2. 任务类型：联合预报任务
3. 是否考虑上游站点：是
4. 需要的子任务：
   - `霍口水库断面预报`
   - `霍口水库~山仔水库区间断面预报`
5. 对应 `stationCode`：
   - `33c76b8bd9384486a945c2fc7fd622eb`
   - `20001`

```json
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "2023-06-30 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 5.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 4.8},
    {"time": "2023-06-30 00:00:00", "stationCode": "20001", "rainfallValue": 6.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "20001", "rainfallValue": 3.8}
  ],
  "modelForecastRainfallParams": [
    {"time": "2023-07-01 01:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 10.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 11.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 8.3},
    {"time": "2023-07-01 01:00:00", "stationCode": "20001", "rainfallValue": 7.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "20001", "rainfallValue": 8.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "20001", "rainfallValue": 7.3}
  ],
  "modelRunParam": {
    "XAJ": []
  }
}
```

## 示例 2：联合预报 — 敖江流域未来 3 小时最终出口流量

1. 目标站点：主干流最终出口 `temp-2`
2. 任务类型：联合预报任务
3. 是否考虑上游站点：是
4. 需要的子任务：
   - `霍口水库断面预报`
   - `霍口水库~山仔水库区间断面预报`
   - `山仔水库~temp-1 区间断面预报`
   - `temp-1~temp-2 区间断面预报`
   - `桂湖溪流域出口断面预报`
   - `牛溪流域出口断面预报`
5. 对应 `stationCode`：
   - `33c76b8bd9384486a945c2fc7fd622eb`
   - `20001`
   - `30001`
   - `40001`
   - `GE2AG000000L`
   - `GE2AF000000R`

```json
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "2023-06-30 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 5.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 4.8},
    {"time": "2023-06-30 00:00:00", "stationCode": "20001", "rainfallValue": 6.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "20001", "rainfallValue": 3.8},
    {"time": "2023-06-30 00:00:00", "stationCode": "30001", "rainfallValue": 5.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "30001", "rainfallValue": 5.8},
    {"time": "2023-06-30 00:00:00", "stationCode": "40001", "rainfallValue": 2.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "40001", "rainfallValue": 2.8},
    {"time": "2023-06-30 00:00:00", "stationCode": "GE2AG000000L", "rainfallValue": 7.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "GE2AG000000L", "rainfallValue": 7.8},
    {"time": "2023-06-30 00:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 1.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 1.8}
  ],
  "modelForecastRainfallParams": [
    {"time": "2023-07-01 01:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 10.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 11.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 8.3},
    {"time": "2023-07-01 01:00:00", "stationCode": "20001", "rainfallValue": 7.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "20001", "rainfallValue": 8.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "20001", "rainfallValue": 7.3},
    {"time": "2023-07-01 01:00:00", "stationCode": "30001", "rainfallValue": 5.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "30001", "rainfallValue": 5.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "30001", "rainfallValue": 5.3},
    {"time": "2023-07-01 01:00:00", "stationCode": "40001", "rainfallValue": 6.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "40001", "rainfallValue": 6.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "40001", "rainfallValue": 6.3},
    {"time": "2023-07-01 01:00:00", "stationCode": "GE2AG000000L", "rainfallValue": 3.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "GE2AG000000L", "rainfallValue": 3.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "GE2AG000000L", "rainfallValue": 3.3},
    {"time": "2023-07-01 01:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 4.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 4.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 4.3}
  ],
  "modelRunParam": {
    "XAJ": []
  }
}
```

## 示例 3：支流出口 — 牛溪流域出口未来 3 小时流量

1. 目标站点：`牛溪流域出口`
2. 任务类型：支流出口计算任务
3. 是否考虑上游站点：否
4. 需要的子任务：`牛溪流域出口断面预报`
5. 对应 `stationCode`：`GE2AF000000R`

```json
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "2023-06-30 00:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 1.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 1.8}
  ],
  "modelForecastRainfallParams": [
    {"time": "2023-07-01 01:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 4.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 4.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "GE2AF000000R", "rainfallValue": 4.3}
  ],
  "modelRunParam": {
    "XAJ": []
  }
}
```

## 示例 4：区间预报 — 霍口水库到山仔水库区间未来 3 小时流量

1. 目标区间：`霍口水库~山仔水库区间`
2. 任务类型：区间预报任务
3. 是否考虑上游站点：否
4. 需要的子任务：`霍口水库~山仔水库区间断面预报`
5. 对应 `stationCode`：`20001`

```json
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "2023-06-30 00:00:00", "stationCode": "20001", "rainfallValue": 1.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "20001", "rainfallValue": 1.8}
  ],
  "modelForecastRainfallParams": [
    {"time": "2023-07-01 01:00:00", "stationCode": "20001", "rainfallValue": 4.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "20001", "rainfallValue": 4.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "20001", "rainfallValue": 4.3}
  ],
  "modelRunParam": {
    "XAJ": []
  }
}
```

## 示例 5：单断面预报 — 霍口水库未来 3 小时入库流量

1. 目标站点：`霍口水库`
2. 任务类型：单断面预报任务
3. 是否考虑上游站点：否
4. 需要的子任务：`霍口水库断面预报`
5. 对应 `stationCode`：`33c76b8bd9384486a945c2fc7fd622eb`

```json
{
  "forecastTime": "2023-07-01 00:00:00",
  "historyDuration": "2",
  "futureDuration": "3",
  "modelDataParams": [
    {"time": "2023-06-30 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 1.2},
    {"time": "2023-07-01 00:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 1.8}
  ],
  "modelForecastRainfallParams": [
    {"time": "2023-07-01 01:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 4.5},
    {"time": "2023-07-01 02:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 4.2},
    {"time": "2023-07-01 03:00:00", "stationCode": "33c76b8bd9384486a945c2fc7fd622eb", "rainfallValue": 4.3}
  ],
  "modelRunParam": {
    "XAJ": []
  }
}
```