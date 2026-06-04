#!/usr/bin/env python3
"""
流量预测脚本

基于时序大模型的洪水预测，支持单变量和协变量模式。

用法:
    python flood_prediction.py --times '["2025-01-01 00:00:00", ...]' --flows '[120.5, 125.3, ...]' [options]

参数:
    --times             历史时间序列（JSON数组，必填）
    --flows             历史流量序列（JSON数组，必填）
    --predict_steps     预测步数，默认 8
    --mode              预测模式：univariate/past_covariates/future_covariates，默认 univariate
    --past_covariates   历史协变量（JSON对象，可选）
    --future_covariates 未来协变量（JSON对象，可选）
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_PIPELINE_INSTANCE = None


def _get_pipeline():
    global _PIPELINE_INSTANCE
    if _PIPELINE_INSTANCE is None:
        try:
            from floodmind.skills.chronos_pipeline import get_pipeline
            _PIPELINE_INSTANCE = get_pipeline()
        except ImportError:
            import torch
            from chronos import Chronos2Pipeline
            device = "cuda" if torch.cuda.is_available() else "cpu"
            _PIPELINE_INSTANCE = Chronos2Pipeline.from_pretrained(
                "amazon/chronos-2",
                device_map=device,
            )
    return _PIPELINE_INSTANCE


def flood_prediction(
    times: List[str],
    flows: List[float],
    predict_steps: int = 8,
    mode: str = "univariate",
    past_covariates: Optional[Dict[str, List[float]]] = None,
    future_covariates: Optional[Dict[str, List[float]]] = None,
 ) -> tuple[str, dict[str, Any]]:
    """
    Chronos-2 时序大模型流量预测。
    
    Args:
        times: 历史时间序列
        flows: 历史流量序列
        predict_steps: 预测步数
        mode: 预测模式
        past_covariates: 历史协变量
        future_covariates: 未来协变量
    
    Returns:
        预测结果字符串
    """
    try:
        if len(times) != len(flows):
            result_text = f"输入错误：时间序列长度({len(times)})与流量序列长度({len(flows)})不一致"
            return result_text, {"success": False, "error": result_text}
        if len(times) < 5:
            result_text = "输入错误：历史数据至少需要5个时间点"
            return result_text, {"success": False, "error": result_text}
        if mode not in ("univariate", "past_covariates", "future_covariates"):
            result_text = f"输入错误：mode 须为 'univariate'、'past_covariates' 或 'future_covariates'，当前值: '{mode}'"
            return result_text, {"success": False, "error": result_text}

        logger.info(f"Chronos-2 预测开始 - 模式: {mode}, 历史点数: {len(times)}, 预测步数: {predict_steps}")

        context_data: Dict = {
            "Time": pd.to_datetime(times),
            "H": [float(v) for v in flows],
        }
        if mode in ("past_covariates", "future_covariates") and past_covariates:
            for name, vals in past_covariates.items():
                context_data[name] = [float(v) for v in vals]

        context_df = pd.DataFrame(context_data)
        context_df = context_df.sort_values("Time").reset_index(drop=True)
        context_df["item_id"] = "item_0"

        time_diffs = context_df["Time"].diff().dropna()
        interval = time_diffs.mode().iloc[0]
        interval_minutes = int(interval.total_seconds() // 60)

        last_time = context_df["Time"].iloc[-1]
        future_times = [last_time + interval * (i + 1) for i in range(predict_steps)]
        future_data: Dict = {
            "Time": future_times,
            "item_id": "item_0",
        }
        if mode == "future_covariates" and future_covariates:
            for name, vals in future_covariates.items():
                future_data[name] = [float(v) for v in vals]

        future_df = pd.DataFrame(future_data)

        pipeline = _get_pipeline()
        pred_df = pipeline.predict_df(
            context_df,
            future_df=future_df,
            prediction_length=predict_steps,
            quantile_levels=[0.1, 0.5, 0.9],
            id_column="item_id",
            timestamp_column="Time",
            target="H",

        )

        def _get_col(df, candidates):
            for c in candidates:
                if c in df.columns:
                    return df[c].values
            return None

        pred_median = _get_col(pred_df, ["0.5", "q0.5", "H_q0.5"])
        pred_low = _get_col(pred_df, ["0.1", "q0.1", "H_q0.1"])
        pred_high = _get_col(pred_df, ["0.9", "q0.9", "H_q0.9"])

        if pred_median is None:
            numeric_cols = pred_df.select_dtypes(include=[np.number]).columns.tolist()
            pred_median = pred_df[numeric_cols[0]].values if numeric_cols else np.zeros(predict_steps)

        covariate_names = list(past_covariates.keys()) if past_covariates else []
        mode_label = {
            "univariate": "单变量",
            "past_covariates": f"含过去协变量（{', '.join(covariate_names)}）",
            "future_covariates": f"含未来协变量（{', '.join(covariate_names)}）",
        }.get(mode, mode)

        lines = [
            "## Chronos-2 洪水流量预测结果",
            "",
            f"| 项目 | 值 |",
            f"|------|-----|",
            f"| 预测模式 | {mode_label} |",
            f"| 历史数据点数 | {len(times)} |",
            f"| 预测步数 | {predict_steps} |",
            f"| 推断时间间隔 | {interval_minutes} 分钟 |",
        ]
        if covariate_names:
            lines.append(f"| 协变量特征 | {', '.join(covariate_names)} |")
        lines += ["", "## 预测结果（中位数 [10% ~ 90% 置信区间]）", "",
                  "| 序号 | 时间 | 流量(m³/s) | 置信区间 |",
                  "|------|------|------------|----------|"]

        for i, t in enumerate(future_times):
            time_str = t.strftime("%Y-%m-%d %H:%M:%S")
            v = pred_median[i]
            if pred_low is not None and pred_high is not None:
                lines.append(
                    f"| {i+1} | {time_str} | {v:.2f} | [{pred_low[i]:.2f} ~ {pred_high[i]:.2f}] |"
                )
            else:
                lines.append(f"| {i+1} | {time_str} | {v:.2f} | - |")

        result_text = "\n".join(lines)
        result_payload = {
            "success": True,
            "mode": mode,
            "mode_label": mode_label,
            "history_points": len(times),
            "predict_steps": predict_steps,
            "interval_minutes": interval_minutes,
            "input": {
                "times": times,
                "flows": flows,
                "past_covariates": past_covariates,
                "future_covariates": future_covariates,
            },
            "predictions": [
                {
                    "index": i + 1,
                    "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "flow": float(pred_median[i]),
                    "q10": None if pred_low is None else float(pred_low[i]),
                    "q90": None if pred_high is None else float(pred_high[i]),
                }
                for i, t in enumerate(future_times)
            ],
            "result_text": result_text,
        }
        logger.info(f"Chronos-2 预测成功，模式: {mode}，共 {predict_steps} 个预测值")
        return result_text, result_payload

    except Exception as e:
        logger.error(f"Chronos-2 预测工具执行失败: {str(e)}", exc_info=True)
        result_text = f"预测工具执行失败: {str(e)}"
        return result_text, {"success": False, "error": result_text}


def main():
    parser = argparse.ArgumentParser(description="Chronos-2 流量预测")
    
    parser.add_argument("--times", required=True, help="历史时间序列（JSON数组）")
    parser.add_argument("--flows", required=True, help="历史流量序列（JSON数组）")
    parser.add_argument("--predict_steps", type=int, default=8, help="预测步数")
    parser.add_argument("--mode", default="univariate", help="预测模式")
    parser.add_argument("--past_covariates", default=None, help="历史协变量（JSON对象）")
    parser.add_argument("--future_covariates", default=None, help="未来协变量（JSON对象）")
    parser.add_argument("--output_file", default=None, help="可选，结构化预测结果 JSON 保存路径")
    
    args = parser.parse_args()
    
    try:
        times = json.loads(args.times)
        flows = json.loads(args.flows)
        
        past_covariates = None
        if args.past_covariates:
            past_covariates = json.loads(args.past_covariates)
        
        future_covariates = None
        if args.future_covariates:
            future_covariates = json.loads(args.future_covariates)
        
        if args.mode == "auto":
            mode = "past_covariates" if past_covariates else "univariate"
        else:
            mode = args.mode
        
        result_text, result_payload = flood_prediction(
            times=times,
            flows=flows,
            predict_steps=args.predict_steps,
            mode=mode,
            past_covariates=past_covariates,
            future_covariates=future_covariates,
        )

        if args.output_file:
            output_path = Path(args.output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            result_text = f"{result_text}\n\n结果已保存到: {output_path}"
        
        print(result_text)
        
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {str(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"错误: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
