"""
工具: query_energy_drivers - 查楼栋能耗驱动因子 (GBM 特征重要性)。

LLM 在节能优化场景用这个工具拿"是什么在驱动这栋楼的能耗"的机器学习结论
(GBM 天气驱动模型的特征重要性), 让节能建议有数据依据, 而不是泛泛而谈。

数据源: mart.prediction_job 里最近一次 SUCCEEDED 的 gbm 预测, 取 result_jsonb
的 feature_importances。没跑过 gbm 预测时返 has_prediction=false, 让 LLM 提示
用户先跑预测。
"""
from __future__ import annotations

from app.services.prediction_service import get_latest


TOOL_NAME = "query_energy_drivers"

# 特征 -> 中文标签 (LLM 写节能建议时用中文标签更自然)
_FEATURE_LABELS = {
    "day_of_week": "星期几",
    "is_weekend": "是否周末",
    "month": "月份(季节)",
    "day_of_year": "年内第几天",
    "air_temp_c": "气温",
    "dew_temp_c": "露点温度",
    "wind_speed_mps": "风速",
    "cloud_cover_pct": "云量",
}

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "查楼栋能耗驱动因子: 基于 GBM 天气驱动机器学习模型的特征重要性, "
            "回答'是什么在驱动这栋楼的能耗' (如气温 40% / 月份 30% / 星期几 15%)。"
            "用于节能优化场景, 让节能建议有 ML 数据依据。只支持单楼, 必传 building_id。"
            "返回 feature_importances (特征重要性降序) + hint 一句话摘要。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "building_id": {
                    "type": "string",
                    "description": "建筑 ID (UUID)。必填。",
                },
            },
            "required": ["building_id"],
        },
    },
}


def call(conn, tenant_id: str, building_id: str | None = None) -> dict:
    """查最近一次 gbm 预测的特征重要性。没跑过预测返 has_prediction=false。"""
    if not building_id:
        raise ValueError("必须传 building_id")

    job = get_latest(tenant_id, building_id, model_type="gbm")
    if job is None or not job.get("result"):
        return {
            "building_id": building_id,
            "has_prediction": False,
            "feature_importances": [],
            "hint": "该楼还没有 GBM 天气驱动预测结果, 建议先在预测页跑一次 gbm 预测",
        }

    result = job["result"]
    fi = result.get("feature_importances") or []
    # 带中文标签 + 百分比, LLM 直接能读懂
    drivers = [
        {
            "feature": f["feature"],
            "label": _FEATURE_LABELS.get(f["feature"], f["feature"]),
            "importance": f["importance"],
            "pct": round(f["importance"] * 100, 1),
        }
        for f in fi
    ]

    if drivers:
        top_desc = ", ".join(
            f"{d['label']} {d['pct']:.0f}%" for d in drivers[:4]
        )
        hint = f"该楼能耗主要驱动因子: {top_desc}"
    else:
        hint = "该楼 gbm 预测无特征重要性数据"

    return {
        "building_id": building_id,
        "has_prediction": True,
        "model_type": "gbm",
        "mape": result.get("mape"),
        "feature_importances": drivers,
        "hint": hint,
    }
